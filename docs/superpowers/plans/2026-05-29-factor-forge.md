# 炼因子 (Factor Forge · SP-B v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 自然语言因子想法 → 截面因子表达式 (LLM + 现成 expr DSL) → 校验/编译/repair → 快测 IC → 入库 (持久化 user 因子, 可被 factor_report 按名引用), 暴露为 `alpha_forge` 对话工具。

**Architecture:** 新 `factors/forge/` 模块: `forge.py` (NL→DSL, LLM 通过可注入 `complete_fn` → 可单测) + `store.py` (UserFactorStore 持久化, 存 DSL 字符串、加载时 `compile_factor` 重建注册)。`alpha_forge`/`user_factors` 工具在 buddy/tools.py (第一个调 LLM 的工具, `asyncio.run(chat)`)。

**Tech Stack:** Python 3.11+, 现成 `LLMClient` / `factors.zoo.expr` / `bench_runner`, numpy/pandas, pytest。无新增依赖。

**Spec:** `docs/superpowers/specs/2026-05-29-factor-forge-design.md`

---

## 关键现状事实 (实现者必读)
- **LLM 调用** (`llm/client.py`): `LLMClient.for_agent("buddy")` (`:100`) 构造; `async chat(messages, tools=None, response_format=None, temperature=0.2)` (`:232`) 返回 dict, 文本 = `resp["choices"][0]["message"]["content"]`。**精确范式见 `wisdom/extractor.py:36-56`**: `client = LLMClient.for_agent(name)`; `resp = await client.chat(messages, response_format={"type":"json_object"}, temperature=0.2)`; `content = resp["choices"][0]["message"]["content"]`; `json.loads(content)`; 2 次重试。
- **async→sync 桥**: buddy 工具体跑在 `asyncio.to_thread` 工作线程 (无运行 loop), 故工具内 `asyncio.run(client.chat(...))` 安全。
- **expr DSL** (`factors/zoo/expr.py`): `FACTOR_VOCAB` (字段 close/open/high/low/volume/vwap/amount/returns/industry + 算子 rank/ts_rank/delta/delay/ts_mean/ts_sum/ts_max/ts_min/stddev/correlation/.../scale/indneutralize) + `validate_expr(expr)` (拒空/`__`/`import`/`lambda`) + `compile_factor(expr) -> (PanelData->Series)`。
- **PanelData** (`factors/zoo/panel.py`): `PanelData(df)` 直接构造, df 是 MultiIndex `(datetime, code)`, 列 open/high/low/close/volume。
- **快测 IC** (factor_test 底层, `buddy/tools.py:_tool_factor_test`): `_resolve_universe_codes(universe)` → `get_default_loader()` → `PanelData.from_loader(loader, codes, since, until, freq="day", industry_loader=ind)` → `AlphaSpec(name, family, description, formula_text, compute)` → `_forward_returns(panel, fwd_days)` (`bench_runner`) → `bench_one(spec, panel, fwd)` → dict `{rank_ic, rank_ir, ic, ir, hit_rate, n_dates, status, state}`。
- **注册表** (`factors/zoo/registry.py`): `_REGISTRY` dict; `register(AlphaSpec)` (同名+同 compute 幂等, 不同 compute raise → user 因子用先 `_REGISTRY.pop(name, None)` 再 register 的替换语义); `AlphaSpec(name, family, description, formula_text, compute, paper="", tags=())` frozen; `get(name)`。
- **工具注册**: `Tool(name, description, input_schema={...}, run=fn, cost_hint=, confirm_required=)` 在 `TOOL_REGISTRY` (`tools.py:1488`); 工具返回 `ToolResult(content: str, is_error: bool=False)` (`tools.py:251`)。`list_tools()` (`:2085`)。
- **写根**: `~/.financial-analyst/cache` (selector `_cache_dir`)。本任务 store 用 `~/.financial-analyst/factors/`, 但 honor `$FINANCIAL_ANALYST_HOME` + 可注入 root (供测试 tmp_path)。
- **测试**: 扁平 `tests/test_*.py`, pytest, `cd /g/financial-analyst && python -m pytest <file> -v`。**dev 环境 pandas 2.3.3** (但包声明 `pandas>=2` → 别用 2.2-only API: 不用 `include_groups=` / `freq="ME"`)。

## 文件结构
**新建:**
- `src/financial_analyst/factors/forge/__init__.py` — 导出 `forge_factor, ForgeResult, UserFactorStore`
- `src/financial_analyst/factors/forge/forge.py` — `ForgeResult` + `forge_factor()` + prompt + repair
- `src/financial_analyst/factors/forge/store.py` — `UserFactorStore`
- `tests/test_factor_forge.py` — forge 单测
- `tests/test_user_factor_store.py` — store 单测
- `tests/test_alpha_forge_tool.py` — 工具单测

**修改:**
- `src/financial_analyst/buddy/tools.py` — `_quick_ic` 助手 + `_tool_alpha_forge` + `_tool_user_factors` + 2 个 Tool 注册
- `src/financial_analyst/buddy/server.py` — `build_app()` 启动时 `register_all()` (1 行, guarded)

---

### Task 1: forge.py — NL→因子表达式 + repair

**Files:**
- Create: `src/financial_analyst/factors/forge/__init__.py`
- Create: `src/financial_analyst/factors/forge/forge.py`
- Create: `tests/test_factor_forge.py`

- [ ] **Step 1: 写失败测试** `tests/test_factor_forge.py`

```python
from __future__ import annotations
import json
import pytest
from financial_analyst.factors.forge.forge import forge_factor, ForgeResult


def _fake(*contents):
    """Return a complete_fn that yields the given canned LLM contents in order."""
    seq = list(contents)
    calls = {"n": 0}
    def fn(messages):
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return c
    fn.calls = calls
    return fn


def test_forge_happy_path():
    good = json.dumps({"expr": "rank(-delta(close,5))", "parsed": [{"k": "方向", "v": "反转"}],
                       "name": "usr_rev5", "rationale": "5日反转", "out_of_vocab": False})
    r = forge_factor("5日反转", complete_fn=_fake(good))
    assert isinstance(r, ForgeResult)
    assert r.compile_ok is True
    assert r.expr == "rank(-delta(close,5))"
    assert r.name == "usr_rev5"
    assert r.out_of_vocab is False


def test_forge_repair_then_succeed():
    bad = json.dumps({"expr": "rank(-delta(close))", "parsed": [], "name": "x", "rationale": "", "out_of_vocab": False})
    good = json.dumps({"expr": "rank(-delta(close,5))", "parsed": [], "name": "usr_rev5", "rationale": "", "out_of_vocab": False})
    fn = _fake(bad, good)
    r = forge_factor("5日反转", complete_fn=fn)
    assert r.compile_ok is True
    assert r.expr == "rank(-delta(close,5))"
    assert fn.calls["n"] == 2  # repaired once


def test_forge_out_of_vocab():
    oov = json.dumps({"expr": "", "parsed": [], "name": "", "rationale": "需要股息率",
                      "out_of_vocab": True, "error": "需要 dv_ttm 字段"})
    r = forge_factor("高股息低负债", complete_fn=_fake(oov))
    assert r.out_of_vocab is True
    assert r.compile_ok is False  # no exception


def test_forge_bad_json():
    r = forge_factor("乱七八糟", complete_fn=_fake("not json at all", "still not json"))
    assert r.compile_ok is False
    assert "JSON" in r.error or "解析" in r.error


def test_forge_empty_idea():
    r = forge_factor("   ", complete_fn=_fake("{}"))
    assert r.compile_ok is False
    assert "想法" in r.error or "idea" in r.error.lower()


def test_forge_uncompilable_expr_after_retries():
    bad = json.dumps({"expr": "close + nonexistent_field", "parsed": [], "name": "x",
                      "rationale": "", "out_of_vocab": False})
    r = forge_factor("怪因子", complete_fn=_fake(bad, bad))
    assert r.compile_ok is False
    assert r.expr == "close + nonexistent_field"  # surfaced for debugging
    assert r.error
```

- [ ] **Step 2: 跑测试确认失败** — `cd /g/financial-analyst && python -m pytest tests/test_factor_forge.py -v` → FAIL `ModuleNotFoundError`.

- [ ] **Step 3: 实现** `src/financial_analyst/factors/forge/forge.py`

```python
"""炼因子: 自然语言想法 → 截面因子表达式 (LLM + expr DSL), 含校验/编译/dry-run/repair。"""
from __future__ import annotations
import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np
import pandas as pd

from financial_analyst.factors.zoo.expr import FACTOR_VOCAB, validate_expr, compile_factor
from financial_analyst.factors.zoo.panel import PanelData

logger = logging.getLogger(__name__)

CompleteFn = Callable[[List[dict]], str]  # messages -> assistant message content


@dataclass
class ForgeResult:
    idea: str
    expr: str = ""
    parsed: List[dict] = field(default_factory=list)
    name: str = ""
    rationale: str = ""
    compile_ok: bool = False
    error: str = ""
    out_of_vocab: bool = False


_SYSTEM = (
    "你是量化因子工程师。把用户的自然语言想法转成 **一个截面因子表达式**, "
    "只能用下列字段+算子 (Python 语法):\n" + FACTOR_VOCAB + "\n"
    "表达式对每个 (日期,股票) 返回一个打分, **高分=更看好** (反转类记得加负号)。\n"
    "若想法需要表中没有的字段 (基本面 pe/pb/股息/ROE/市值, 或'连续/金叉/突破'这类事件条件), "
    "把 out_of_vocab 设 true 并在 rationale 里说明缺什么, expr 留空。\n"
    '只输出 JSON: {"expr": "...", "parsed": [{"k":"触发","v":"..."}], '
    '"name": "usr_xxx", "rationale": "...", "out_of_vocab": false}'
)
_FEWSHOT = [
    {"role": "user", "content": "5日反转"},
    {"role": "assistant", "content": json.dumps({"expr": "rank(-delta(close,5))",
        "parsed": [{"k": "方向", "v": "近5日跌得多→反弹, 负delta"}], "name": "usr_rev5",
        "rationale": "5日动量取负做反转打分", "out_of_vocab": False}, ensure_ascii=False)},
    {"role": "user", "content": "放量上涨"},
    {"role": "assistant", "content": json.dumps({"expr": "rank(delta(close,1)) * rank(volume / ts_mean(volume,20))",
        "parsed": [{"k": "价", "v": "当日上涨"}, {"k": "量", "v": "量比20日均"}], "name": "usr_volup",
        "rationale": "涨幅×相对放量", "out_of_vocab": False}, ensure_ascii=False)},
]


def _build_messages(idea: str, repair_error: Optional[str] = None) -> List[dict]:
    msgs = [{"role": "system", "content": _SYSTEM}] + _FEWSHOT + [{"role": "user", "content": idea}]
    if repair_error:
        msgs.append({"role": "user", "content":
                     f"上一版表达式有问题: {repair_error}。请只用允许的字段+算子, 重出 JSON。"})
    return msgs


def _default_complete(messages: List[dict]) -> str:
    from financial_analyst.llm.client import LLMClient
    client = LLMClient.for_agent("buddy")
    resp = asyncio.run(client.chat(messages, response_format={"type": "json_object"}, temperature=0.2))
    return resp["choices"][0]["message"]["content"]


def _tiny_panel() -> PanelData:
    dates = pd.date_range("2024-01-01", periods=12, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C", "D"]], names=["datetime", "code"])
    rng = np.random.default_rng(0)
    rets = pd.Series(rng.lognormal(0.0, 0.02, len(idx)), index=idx)
    close = rets.groupby(level="code").cumprod() * 50 + 10
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                       "close": close, "volume": pd.Series(1e6, index=idx)})
    return PanelData(df)


def forge_factor(idea: str, complete_fn: Optional[CompleteFn] = None) -> ForgeResult:
    """Turn a natural-language idea into a validated cross-sectional factor expression.

    The LLM is reached via ``complete_fn(messages) -> content_str`` (injected for tests;
    defaults to the buddy LLMClient). Tries up to 2 times, feeding a compile/parse error
    back on the second attempt (repair). Never raises — failures land in ForgeResult.error.
    """
    idea = (idea or "").strip()
    if not idea:
        return ForgeResult(idea="", error="缺少想法 (idea)")
    complete = complete_fn or _default_complete
    res = ForgeResult(idea=idea)
    repair_error: Optional[str] = None

    for _attempt in range(2):
        try:
            content = complete(_build_messages(idea, repair_error))
        except Exception as e:
            return ForgeResult(idea=idea, error=f"LLM 调用失败: {type(e).__name__}: {e}")
        try:
            obj = json.loads(content)
        except Exception as e:
            repair_error = f"输出非合法 JSON: {e}"
            res.error = "LLM 输出无法解析为 JSON"
            continue

        res.parsed = obj.get("parsed") or []
        res.name = (obj.get("name") or "").strip()
        res.rationale = (obj.get("rationale") or "").strip()
        res.out_of_vocab = bool(obj.get("out_of_vocab", False))
        if res.out_of_vocab:
            res.compile_ok = False
            res.error = res.rationale or "想法需要当前价量 DSL 没有的字段/事件条件 (基本面→SP-B.1b, 事件→SP-B.2)"
            return res

        expr = (obj.get("expr") or "").strip()
        if not expr:
            repair_error = "expr 为空"
            res.error = "未生成表达式"
            continue
        try:
            validate_expr(expr)
            fn = compile_factor(expr)
            out = fn(_tiny_panel())
            if not isinstance(out, pd.Series):
                raise TypeError(f"表达式返回 {type(out).__name__}, 应为 pd.Series")
            res.expr, res.compile_ok, res.error = expr, True, ""
            return res
        except Exception as e:
            repair_error = f"{type(e).__name__}: {e}"
            res.expr = expr
            res.error = f"表达式无法编译/运行: {e}"
            continue

    return res  # compile_ok=False after 2 attempts
```

And `src/financial_analyst/factors/forge/__init__.py`:
```python
"""炼因子 (SP-B): 自然语言 → 因子 + 用户因子持久化。"""
from financial_analyst.factors.forge.forge import forge_factor, ForgeResult
from financial_analyst.factors.forge.store import UserFactorStore

__all__ = ["forge_factor", "ForgeResult", "UserFactorStore"]
```
(NOTE: this imports `store` — create a minimal `store.py` stub now OR do Task 2 first. To keep Task 1 self-contained, TEMPORARILY make `__init__.py` only import forge for Task 1, then add `UserFactorStore` in Task 2. Use this Task-1 `__init__.py`:)
```python
"""炼因子 (SP-B): 自然语言 → 因子。"""
from financial_analyst.factors.forge.forge import forge_factor, ForgeResult

__all__ = ["forge_factor", "ForgeResult"]
```

- [ ] **Step 4: 跑测试确认通过** — `cd /g/financial-analyst && python -m pytest tests/test_factor_forge.py -v` → 6 passed.

- [ ] **Step 5: Commit**
```bash
cd /g/financial-analyst && git add src/financial_analyst/factors/forge/__init__.py src/financial_analyst/factors/forge/forge.py tests/test_factor_forge.py && git commit -m "feat(forge): NL idea -> cross-sectional factor expression (LLM + validate/compile/repair)"
```

---

### Task 2: store.py — UserFactorStore (持久化 + 重建注册)

**Files:**
- Create: `src/financial_analyst/factors/forge/store.py`
- Modify: `src/financial_analyst/factors/forge/__init__.py` (加 `UserFactorStore` 导出)
- Create: `tests/test_user_factor_store.py`

- [ ] **Step 1: 写失败测试** `tests/test_user_factor_store.py`

```python
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest
from financial_analyst.factors.forge.store import UserFactorStore
from financial_analyst.factors.zoo import PanelData
from financial_analyst.factors.zoo.registry import get as reg_get, _clear_registry_for_tests


def _panel():
    dates = pd.date_range("2024-01-01", periods=12, freq="B")
    idx = pd.MultiIndex.from_product([dates, ["A", "B", "C", "D"]], names=["datetime", "code"])
    rng = np.random.default_rng(1)
    close = pd.Series(rng.lognormal(0, 0.02, len(idx)), index=idx).groupby(level="code").cumprod() * 50 + 10
    return PanelData(pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99,
                                   "close": close, "volume": pd.Series(1e6, index=idx)}))


def test_add_persists_and_registers(tmp_path):
    s = UserFactorStore(root=tmp_path)
    entry = s.add({"name": "usr_rev5", "family": "user", "expr": "rank(-delta(close,5))",
                   "description": "5日反转", "parsed": [], "kpis": {}})
    assert entry["name"] == "usr_rev5"
    # persisted
    assert (tmp_path / "user_factors.json").exists()
    assert UserFactorStore(root=tmp_path).list()[0]["name"] == "usr_rev5"
    # registered + compute works on a panel
    spec = reg_get("usr_rev5")
    assert spec.family == "user"
    out = spec.compute(_panel())
    assert isinstance(out, pd.Series)


def test_reload_register_all(tmp_path):
    UserFactorStore(root=tmp_path).add({"name": "usr_x", "family": "user",
        "expr": "rank(close)", "description": "", "parsed": [], "kpis": {}})
    _clear_registry_for_tests()  # simulate fresh process
    with pytest.raises(KeyError):
        reg_get("usr_x")
    n = UserFactorStore(root=tmp_path).register_all()
    assert n == 1
    assert reg_get("usr_x").family == "user"


def test_dup_name_gets_suffix(tmp_path):
    s = UserFactorStore(root=tmp_path)
    a = s.add({"name": "usr_x", "family": "user", "expr": "rank(close)", "description": "", "parsed": [], "kpis": {}})
    b = s.add({"name": "usr_x", "family": "user", "expr": "rank(-close)", "description": "", "parsed": [], "kpis": {}})
    assert a["name"] == "usr_x"
    assert b["name"] == "usr_x_2"


def test_remove(tmp_path):
    s = UserFactorStore(root=tmp_path)
    s.add({"name": "usr_x", "family": "user", "expr": "rank(close)", "description": "", "parsed": [], "kpis": {}})
    assert s.remove("usr_x") is True
    assert s.list() == []
    assert s.remove("usr_x") is False  # already gone


def test_missing_file_is_empty(tmp_path):
    s = UserFactorStore(root=tmp_path / "nope")
    assert s.list() == []
    assert s.register_all() == 0
```

- [ ] **Step 2: 跑测试确认失败** — `cd /g/financial-analyst && python -m pytest tests/test_user_factor_store.py -v` → FAIL `ImportError`.

- [ ] **Step 3: 实现** `src/financial_analyst/factors/forge/store.py`

```python
"""用户炼出的因子库: 持久化 DSL 字符串, 加载时 compile_factor 重建并注册 (family='user')。"""
from __future__ import annotations
import json
import logging
import os
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


def _default_factors_root() -> Path:
    home = os.environ.get("FINANCIAL_ANALYST_HOME")
    base = Path(home) if home else (Path.home() / ".financial-analyst")
    return base / "factors"


class UserFactorStore:
    """JSON-backed store of user factors. Each entry:
    {name, family, expr, description, parsed, created, kpis}. ``expr`` (the DSL string)
    is the source of truth — compute fns are recompiled on load (AlphaSpec.compute can't
    be serialized)."""

    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root is not None else _default_factors_root()
        self.path = self.root / "user_factors.json"

    def load(self) -> List[dict]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception as e:
            logger.warning("user_factors.json 损坏, 当空处理: %s", e)
            return []

    def save(self, entries: List[dict]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(entries, ensure_ascii=False, indent=2), encoding="utf-8")

    def _unique_name(self, name: str, taken: set) -> str:
        name = name or "usr_factor"
        if name not in taken:
            return name
        i = 2
        while f"{name}_{i}" in taken:
            i += 1
        return f"{name}_{i}"

    def add(self, entry: dict) -> dict:
        entries = self.load()
        entry = dict(entry)
        entry["name"] = self._unique_name(entry.get("name", ""), {e["name"] for e in entries})
        entries.append(entry)
        self.save(entries)
        self.register_one(entry)
        return entry

    def list(self) -> List[dict]:
        return self.load()

    def remove(self, name: str) -> bool:
        entries = self.load()
        kept = [e for e in entries if e.get("name") != name]
        if len(kept) == len(entries):
            return False
        self.save(kept)
        from financial_analyst.factors.zoo import registry as _reg
        _reg._REGISTRY.pop(name, None)
        return True

    def register_one(self, entry: dict) -> None:
        from financial_analyst.factors.zoo.expr import compile_factor
        from financial_analyst.factors.zoo.registry import AlphaSpec, register, _REGISTRY
        name = entry["name"]
        _REGISTRY.pop(name, None)  # replace: recompiled compute is a new fn (avoids frozen-collision raise)
        register(AlphaSpec(name=name, family="user",
                           description=entry.get("description", ""),
                           formula_text=entry.get("expr", ""),
                           compute=compile_factor(entry["expr"])))

    def register_all(self) -> int:
        n = 0
        for entry in self.load():
            try:
                self.register_one(entry)
                n += 1
            except Exception as e:
                logger.warning("user 因子 %r 重建失败: %s", entry.get("name"), e)
        return n
```

Update `src/financial_analyst/factors/forge/__init__.py`:
```python
"""炼因子 (SP-B): 自然语言 → 因子 + 用户因子持久化。"""
from financial_analyst.factors.forge.forge import forge_factor, ForgeResult
from financial_analyst.factors.forge.store import UserFactorStore

__all__ = ["forge_factor", "ForgeResult", "UserFactorStore"]
```

- [ ] **Step 4: 跑测试确认通过** — `cd /g/financial-analyst && python -m pytest tests/test_user_factor_store.py tests/test_factor_forge.py -v` → all pass.

- [ ] **Step 5: Commit**
```bash
cd /g/financial-analyst && git add src/financial_analyst/factors/forge/store.py src/financial_analyst/factors/forge/__init__.py tests/test_user_factor_store.py && git commit -m "feat(forge): UserFactorStore — persist DSL string + recompile/register on load"
```

---

### Task 3: alpha_forge / user_factors 工具 + 快测助手 + 启动重建

**Files:**
- Modify: `src/financial_analyst/buddy/tools.py` (加 `_quick_ic` + `_tool_alpha_forge` + `_tool_user_factors` + 2 Tool 注册)
- Modify: `src/financial_analyst/buddy/server.py` (`build_app()` 启动 `register_all()`)
- Create: `tests/test_alpha_forge_tool.py`

- [ ] **Step 1: 写失败测试** `tests/test_alpha_forge_tool.py`

```python
from __future__ import annotations
import numpy as np
import pandas as pd
import pytest


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
    return StubLoader()


def _ok_forge(name="usr_rev5", expr="rank(-delta(close,5))"):
    from financial_analyst.factors.forge import ForgeResult
    return ForgeResult(idea="5日反转", expr=expr, parsed=[{"k": "方向", "v": "反转"}],
                       name=name, rationale="5日反转", compile_ok=True)


def test_alpha_forge_runs_no_save(monkeypatch):
    from financial_analyst.buddy import tools as t
    monkeypatch.setattr("financial_analyst.factors.forge.forge_factor", lambda idea, **k: _ok_forge())
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes",
                        lambda u: ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750"])
    monkeypatch.setattr("financial_analyst.data.loader_factory.get_default_loader", lambda: _stub_loader())
    res = t._tool_alpha_forge(idea="5日反转", save=False, universe="csi500")
    assert res.is_error is False
    assert "rank(-delta(close,5))" in res.content
    assert "RankIC" in res.content


def test_alpha_forge_out_of_vocab_is_error(monkeypatch):
    from financial_analyst.buddy import tools as t
    from financial_analyst.factors.forge import ForgeResult
    monkeypatch.setattr("financial_analyst.factors.forge.forge_factor",
                        lambda idea, **k: ForgeResult(idea=idea, out_of_vocab=True, compile_ok=False,
                                                       error="需要 dv_ttm 基本面字段"))
    res = t._tool_alpha_forge(idea="高股息", save=False)
    assert res.is_error is True
    assert "dv_ttm" in res.content or "基本面" in res.content


def test_alpha_forge_save_registers(tmp_path, monkeypatch):
    from financial_analyst.buddy import tools as t
    from financial_analyst.factors.zoo.registry import get as reg_get
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    monkeypatch.setattr("financial_analyst.factors.forge.forge_factor", lambda idea, **k: _ok_forge(name="usr_saved"))
    monkeypatch.setattr("financial_analyst.data.universe.resolve_universe_codes",
                        lambda u: ["SH600519", "SZ000858", "SH600036", "SH601318", "SZ300750"])
    monkeypatch.setattr("financial_analyst.data.loader_factory.get_default_loader", lambda: _stub_loader())
    res = t._tool_alpha_forge(idea="5日反转", save=True, universe="csi500")
    assert res.is_error is False
    assert reg_get("usr_saved").family == "user"  # now referenceable by factor_report


def test_user_factors_lists(tmp_path, monkeypatch):
    from financial_analyst.buddy import tools as t
    from financial_analyst.factors.forge import UserFactorStore
    UserFactorStore(root=tmp_path / "factors").add({"name": "usr_a", "family": "user",
        "expr": "rank(close)", "description": "d", "parsed": [], "kpis": {}})
    monkeypatch.setenv("FINANCIAL_ANALYST_HOME", str(tmp_path))
    res = t._tool_user_factors()
    assert res.is_error is False
    assert "usr_a" in res.content


def test_forge_and_user_factors_registered():
    from financial_analyst.buddy.tools import TOOL_REGISTRY
    names = {x.name for x in TOOL_REGISTRY}
    assert "alpha_forge" in names and "user_factors" in names
```

- [ ] **Step 2: 跑测试确认失败** — `cd /g/financial-analyst && python -m pytest tests/test_alpha_forge_tool.py -v` → FAIL `AttributeError: _tool_alpha_forge`.

- [ ] **Step 3: 在 `buddy/tools.py` 加助手 + 两个工具函数** (放在 `_tool_alpha_compare` 之后)

```python
def _quick_ic(compute_fn, universe: str, since: str, until: str,
              fwd_days: int = 5, max_codes: int = 120) -> dict:
    """Quick cross-sectional IC of a compute fn (reuses factor_test's bench path)."""
    import warnings
    import numpy as np
    from financial_analyst.data.loader_factory import get_default_loader
    from financial_analyst.factors.zoo.panel import PanelData
    from financial_analyst.factors.zoo.bench_runner import _forward_returns, bench_one
    from financial_analyst.factors.zoo.registry import AlphaSpec
    codes = _resolve_universe_codes(universe)
    if not codes:
        return {"status": "no_universe"}
    codes = codes[: max(20, min(int(max_codes), len(codes)))]
    loader = get_default_loader()
    try:
        from financial_analyst.data.loaders.industry import IndustryLoader, industry_map_path
        ind = IndustryLoader() if industry_map_path().exists() else None
    except Exception:
        ind = None
    panel = PanelData.from_loader(loader, codes, since, until, freq="day", industry_loader=ind)
    spec = AlphaSpec(name="__forge__", family="custom", description="", formula_text="", compute=compute_fn)
    fwd = _forward_returns(panel, int(fwd_days))
    with warnings.catch_warnings(), np.errstate(invalid="ignore", divide="ignore"):
        warnings.simplefilter("ignore")
        return bench_one(spec, panel, fwd)


def _tool_alpha_forge(idea: str, save: bool = False, universe: str = "csi300_active",
                      since: str = "2024-01-01", until: str = "2024-12-31",
                      quick_eval: bool = True) -> ToolResult:
    """炼因子: 自然语言想法 → 截面因子表达式 + 快测 IC, 可入库 (之后可被 factor_report 引用)。"""
    import math
    from financial_analyst.factors import forge as _forge_mod
    fr = _forge_mod.forge_factor(idea)
    if fr.out_of_vocab:
        return ToolResult(f"这个想法当前价量 DSL 炼不了: {fr.error}\n"
                          f"(基本面字段→SP-B.1b, 事件信号→SP-B.2)", is_error=True)
    if not fr.compile_ok:
        return ToolResult(f"炼因子失败: {fr.error}", is_error=True)

    lines = [f"# 炼因子 · {fr.name}", f"原话: {idea}", "", "解析:"]
    lines += [f"  · {p.get('k')}: {p.get('v')}" for p in fr.parsed]
    lines += ["", f"公式: {fr.expr}", f"逻辑: {fr.rationale}"]

    kpis: dict = {}
    if quick_eval:
        from financial_analyst.factors.zoo.expr import compile_factor
        try:
            kpis = _quick_ic(compile_factor(fr.expr), universe, since, until)
            if kpis.get("status") == "ok":
                def f(x):
                    return "—" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.4f}"
                lines += ["", f"快测 IC ({universe}): RankIC={f(kpis.get('rank_ic'))} "
                          f"RankICIR={f(kpis.get('rank_ir'))} 命中={kpis.get('hit_rate')} 【{kpis.get('state')}】"]
            else:
                lines += ["", f"快测跳过 (universe={universe} 无法解析)"]
        except Exception as e:
            lines += ["", f"快测失败: {type(e).__name__}: {e}"]

    if save:
        from financial_analyst.factors.forge import UserFactorStore
        entry = UserFactorStore().add({"name": fr.name, "family": "user", "expr": fr.expr,
                                       "description": fr.rationale[:60], "parsed": fr.parsed,
                                       "kpis": kpis if kpis.get("status") == "ok" else {}})
        lines += ["", f"✓ 已入库: {entry['name']} — 可 `factor_report {entry['name']}` 跑完整评测"]
    else:
        lines += ["", "(save=true 入库后可被 factor_report / alpha_compare 按名引用)"]
    return ToolResult("\n".join(lines))


def _tool_user_factors(remove: str = "") -> ToolResult:
    """列出已入库的 user 因子; remove=<name> 删除一个。"""
    from financial_analyst.factors.forge import UserFactorStore
    store = UserFactorStore()
    if remove:
        ok = store.remove(remove)
        return ToolResult(f"{'已删除' if ok else '未找到'} user 因子: {remove}")
    rows = store.list()
    if not rows:
        return ToolResult("暂无已入库 user 因子。用 alpha_forge(idea=..., save=true) 炼一个。")
    lines = [f"# 已入库 user 因子 ({len(rows)})"]
    for e in rows:
        k = e.get("kpis") or {}
        ric = k.get("rank_ic")
        lines.append(f"· {e['name']}  {e.get('expr','')}"
                     + (f"  (RankIC={ric:+.4f})" if isinstance(ric, (int, float)) else ""))
    return ToolResult("\n".join(lines))
```

- [ ] **Step 4: 注册两个 Tool** — 在 `TOOL_REGISTRY` 列表 `alpha_compare` 之后插入:

```python
    Tool(
        name="alpha_forge",
        description=(
            "炼因子: 把一句【自然语言因子想法】炼成截面因子表达式 (用价量 DSL) + 快测它的 IC。"
            "用于用户说「帮我把 xx 想法做成因子」「炼一个 yy 因子」。save=true 入库后该因子可被 "
            "factor_report / alpha_compare 按名引用。注: 当前只支持价量类想法 (动量/反转/量价/波动); "
            "基本面(股息/估值/市值)或事件型(连续/金叉/突破)暂不支持, 会提示。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "idea": {"type": "string", "description": "自然语言因子想法, 如 '5日反转' / '放量上涨' / '低波动'"},
                "save": {"type": "boolean", "default": False, "description": "true=炼好入库 (可被 factor_report 引用)"},
                "universe": {"type": "string", "default": "csi300_active"},
                "since": {"type": "string", "default": "2024-01-01"},
                "until": {"type": "string", "default": "2024-12-31"},
                "quick_eval": {"type": "boolean", "default": True},
            },
            "required": ["idea"],
        },
        run=_tool_alpha_forge,
        cost_hint="minutes",
        confirm_required=True,
    ),
    Tool(
        name="user_factors",
        description="列出已入库的 user 因子 (名/表达式/IC); remove=<name> 删除一个。",
        input_schema={
            "type": "object",
            "properties": {"remove": {"type": "string", "description": "要删除的 user 因子名 (留空=只列出)"}},
        },
        run=_tool_user_factors,
        cost_hint="fast",
    ),
```

- [ ] **Step 5: 启动重建注册** — in `src/financial_analyst/buddy/server.py` `build_app()`, near the top of the function body (after the app is created / before returning), add (guarded):

```python
    # SP-B: 重建注册已入库的 user 炼因子 (DSL 字符串 → compile → register family='user')
    try:
        from financial_analyst.factors.forge import UserFactorStore
        UserFactorStore().register_all()
    except Exception:
        pass
```
(READ `build_app()` first to place this inside it; if the function is large, put it right after the FastAPI `app = FastAPI(...)` line. This makes forged factors available to `factor_report`/`alpha_compare` in future sessions.)

- [ ] **Step 6: 跑测试确认通过 + 回归** — `cd /g/financial-analyst && python -m pytest tests/test_alpha_forge_tool.py tests/test_factor_forge.py tests/test_user_factor_store.py tests/test_buddy.py -v` → all pass.

- [ ] **Step 7: Commit**
```bash
cd /g/financial-analyst && git add src/financial_analyst/buddy/tools.py src/financial_analyst/buddy/server.py tests/test_alpha_forge_tool.py && git commit -m "feat(buddy): alpha_forge + user_factors tools + startup re-register of user factors"
```

---

## 收尾 (全部任务后)
- [ ] 全量回归: `cd /g/financial-analyst && python -m pytest tests/test_factor_forge.py tests/test_user_factor_store.py tests/test_alpha_forge_tool.py tests/test_factor_eval.py tests/test_buddy.py tests/test_factor_zoo.py -q`
- [ ] Dispatch final code-reviewer (整个 SP-B diff)
- [ ] `superpowers:finishing-a-development-branch` 收尾

## 自检 (写计划时已过)
**Spec 覆盖:** forge_factor NL→expr+repair(T1) / out_of_vocab(T1,T3) / UserFactorStore 持久化+重编译注册(T2) / alpha_forge 工具+快测IC+入库(T3) / user_factors 列删(T3) / 启动重建(T3) / 截面 v1 价量 DSL(贯穿) / 错误结构化不崩(T1 forge + T3 工具) / mock LLM 测试(T1 complete_fn, T3 monkeypatch forge_factor)。✅ 全覆盖。基本面=SP-B.1b、事件=SP-B.2、召回/UI 明确 out-of-scope。

**占位符扫描:** 无 TBD/TODO; 每个改代码 step 有完整代码 + 命令 + 预期。✅ (server.py 的 build_app 插入点让实现者先 READ 定位, 因为该函数具体行号会变。)

**类型一致:** `ForgeResult` 字段 (idea/expr/parsed/name/rationale/compile_ok/error/out_of_vocab) T1 定义, T3 用 `.out_of_vocab/.compile_ok/.expr/.name/.parsed/.rationale/.error` 一致; `UserFactorStore(root=)` + `add/list/remove/register_all/register_one` T2 定义, T3 用 add/list/remove 一致; `forge_factor(idea, complete_fn=)` T1 定义, T3 monkeypatch `financial_analyst.factors.forge.forge_factor` (注: T3 工具内 `from financial_analyst.factors import forge as _forge_mod; _forge_mod.forge_factor(idea)` — 故 monkeypatch 目标是 `financial_analyst.factors.forge.forge_factor`, 即包 __init__ 导出的名, 与工具内引用一致)。`_quick_ic` 返回 bench_one 的 dict (rank_ic/rank_ir/hit_rate/state/status)。✅
