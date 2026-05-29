# 多因子合成 LLM 赋能 (SP-D.2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给多因子合成加 LLM 层 — 输入顾问 (NL 目标→合成配方) + 结果研判 (OOS 结果→质性解读), 形成 说目标→配方→跑→研判→迭代 闭环。

**Architecture:** 新 `factors/compose/advisor.py` 放全部 LLM (镜像 forge: 可注入 `complete_fn`、`asyncio.run`、2 次 repair、永不抛); 纯 `compose_factors` 引擎不碰 LLM。REST 加 advise 端点 + compose 加 interpret 字段 (端点改 sync def 因 asyncio.run); agent 工具 + 工作台接上。

**Tech Stack:** Python / pandas / FastAPI TestClient / LLMClient (复用)。无新依赖。

**纪律:** 测试用 `D:\app\miniconda` python 在 `G:\financial-analyst` 跑 `python -m pytest`; LLM 经**可注入 complete_fn** 单测 (不真调 LLM); 端点/工具经**模块属性访问** (`_advisor_mod.xxx`) 便于 monkeypatch; 不用 pandas≥2.2-only API; 不污染注册表。

---

## File Structure

- **Create** `src/financial_analyst/factors/compose/advisor.py` — `ComposeRecipe` + `compose_advisor` (NL→配方) + `interpret_compose` (结果→研判) + LLM 助手。
- **Modify** `src/financial_analyst/buddy/server.py` — `AdviseReq` + `POST /factor/compose/advise`; `ComposeReq` 加 `interpret`; `factor_compose_ep` async→sync def + interpret。
- **Modify** `src/financial_analyst/buddy/tools.py` — `_tool_factor_compose` 加 `goal` + 自动研判; 注册表 input_schema 更新。
- **Modify** `src/financial_analyst/ui/quant.jsx` — `ComposeMode` 加「一句话配方」+ 研判 panel。
- **Modify** `src/financial_analyst/ui/quant.html` — bump `?v=`。
- **Create** `tests/test_compose_llm.py` — advisor + interpret + REST 全套 (stub complete_fn)。

---

## Task 1: `advisor.py` — compose_advisor + interpret_compose

**Files:**
- Create: `src/financial_analyst/factors/compose/advisor.py`
- Test: `tests/test_compose_llm.py`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_compose_llm.py`:

```python
"""SP-D.2 多因子合成 LLM: 输入顾问 + 结果研判。"""
from __future__ import annotations

import json
import pytest

import financial_analyst.factors.zoo  # noqa: F401  (注册 alpha families)
from financial_analyst.factors.compose.advisor import (
    ComposeRecipe, compose_advisor, interpret_compose)


def _canned(payload):
    """返回一个 complete_fn, 它忽略 messages 总是返回 json.dumps(payload)。"""
    return lambda messages: json.dumps(payload, ensure_ascii=False)


def test_compose_advisor_ok():
    fn = _canned({"members": ["rank(-delta(close,5))", "rank(delta(close,20))"],
                  "method": "linear", "train_frac": 0.65, "rationale": "反转+动量互补"})
    rec = compose_advisor("低回撤动量反转", complete_fn=fn)
    assert isinstance(rec, ComposeRecipe)
    assert rec.status == "ok"
    assert len(rec.members) == 2
    assert rec.method == "linear"
    assert rec.train_frac == pytest.approx(0.65)
    assert "互补" in rec.rationale


def test_compose_advisor_clamps_and_defaults():
    fn = _canned({"members": ["rank(close)", "rank(volume)"],
                  "method": "bogus", "train_frac": 0.99, "rationale": "x"})
    rec = compose_advisor("随便", complete_fn=fn)
    assert rec.status == "ok"
    assert rec.method == "lgbm"            # 非法方法 → 默认 lgbm
    assert rec.train_frac == pytest.approx(0.8)   # clip 到 0.8


def test_compose_advisor_repairs_bad_member():
    calls = {"n": 0}
    def fn(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return json.dumps({"members": ["rank(nonexistent_field)", "rank(close)"],
                               "method": "equal", "train_frac": 0.6, "rationale": "first"})
        return json.dumps({"members": ["rank(-delta(close,5))", "rank(close)"],
                           "method": "equal", "train_frac": 0.6, "rationale": "fixed"})
    rec = compose_advisor("目标", complete_fn=fn)
    assert rec.status == "ok" and calls["n"] == 2   # 第一次烂成员→repair→第二次成功
    assert rec.rationale == "fixed"


def test_compose_advisor_bad_output():
    rec = compose_advisor("目标", complete_fn=_canned({"members": ["rank(close)"], "method": "equal"}))
    assert rec.status == "bad_output"   # 成员 <2, 两次都不行


def test_compose_advisor_llm_error():
    def boom(messages):
        raise RuntimeError("llm down")
    rec = compose_advisor("目标", complete_fn=boom)
    assert rec.status == "llm_error" and "llm down" in rec.error


class _FakeIC:
    rank_ic_mean = 0.03
class _FakePf:
    sharpe = 0.9
    ann_return = 0.2
    max_drawdown = -0.1
class _FakeComposite:
    ic = _FakeIC()
    portfolio = _FakePf()
class _FakeMember:
    def __init__(self, name, ric, sh):
        self.name, self.rank_ic, self.sharpe = name, ric, sh
class _FakeResult:
    status = "ok"
    method = "lgbm"
    weights = {"rank(close)": 0.7, "rank(volume)": 0.3}
    member_oos = [_FakeMember("rank(close)", 0.02, 0.6), _FakeMember("rank(volume)", 0.01, 0.4)]
    composite = _FakeComposite()
    n_train_dates = 20
    n_test_dates = 12
    verdict = "综合分 Sharpe 0.9 vs 最优成员 0.6 → 增益"


def test_interpret_compose_ok():
    res = interpret_compose(_FakeResult(), complete_fn=lambda m: "权重集中在 rank(close), 注意过拟合; 建议加正交化。")
    assert "过拟合" in res


def test_interpret_compose_failure_returns_empty():
    res = interpret_compose(_FakeResult(), complete_fn=lambda m: (_ for _ in ()).throw(RuntimeError("x")))
    assert res == ""


def test_interpret_compose_non_ok_returns_empty():
    class _Bad:
        status = "load_error"
    assert interpret_compose(_Bad(), complete_fn=lambda m: "should not be called") == ""
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_compose_llm.py -q`
Expected: FAIL (`ModuleNotFoundError: ...factors.compose.advisor`)

- [ ] **Step 3: 实现 `advisor.py`**

写 `src/financial_analyst/factors/compose/advisor.py`:

```python
"""多因子合成 LLM 层 (SP-D.2) — 输入顾问 (NL→配方) + 结果研判。

compose_advisor: 自然语言目标 → ComposeRecipe (成员表达式 + 方法 + 理由), 校验 + repair。
interpret_compose: ComposeResult → 质性研判串。两者经可注入 complete_fn (测试), 默认走
buddy LLMClient (asyncio.run, 须无事件循环线程)。永不抛。纯 compose_factors 引擎不碰 LLM。
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import numpy as np
import pandas as pd

from financial_analyst.factors.zoo.expr import FACTOR_VOCAB, validate_expr, compile_factor
from financial_analyst.factors.zoo.panel import PanelData

CompleteFn = Callable[[List[dict]], str]
_METHODS = ("equal", "ic_weighted", "linear", "lgbm")


@dataclass
class ComposeRecipe:
    goal: str
    members: List[str] = field(default_factory=list)
    method: str = "lgbm"
    train_frac: float = 0.6
    rationale: str = ""
    status: str = "ok"        # ok / bad_output / llm_error
    error: str = ""


def _complete_json(messages: List[dict]) -> str:
    from financial_analyst.llm.client import LLMClient
    client = LLMClient.for_agent("buddy")
    resp = asyncio.run(client.chat(messages, response_format={"type": "json_object"}, temperature=0.2))
    return resp["choices"][0]["message"]["content"]


def _complete_text(messages: List[dict]) -> str:
    from financial_analyst.llm.client import LLMClient
    client = LLMClient.for_agent("buddy")
    resp = asyncio.run(client.chat(messages, temperature=0.3))
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


def _member_error(expr: str, panel) -> Optional[str]:
    """成员表达式错误信息 (None=通过): validate + compile + 小面板 dry-run。"""
    try:
        validate_expr(expr)
        out = compile_factor(expr)(panel)
    except Exception as e:
        return f"{expr!r}: {type(e).__name__}: {e}"
    if not isinstance(out, pd.Series):
        return f"{expr!r}: 求值非 Series ({type(out).__name__})"
    return None


_ADVISOR_SYSTEM = (
    "你是量化多因子组合研究员。把用户的目标拆成 **>=2 个互补的截面因子表达式**, "
    "只用下列字段+算子 (Python 语法):\n" + FACTOR_VOCAB + "\n"
    "再选合成方法: equal(等权,少而稳)/ic_weighted(按IC加权)/linear(线性回归去冗余)/lgbm(非线性,成员多时); "
    "成员可能同源时优先 linear/lgbm。给 train_frac (0.5~0.7) 和简短理由。"
    "不要用 Python 内置函数, 只用算子表里的 (abs_/max_pair/min_pair 等)。\n"
    '只输出 JSON: {"members": ["expr1","expr2"], "method": "lgbm", "train_frac": 0.6, "rationale": "..."}'
)
_ADVISOR_FEWSHOT = [
    {"role": "user", "content": "低回撤的动量+反转组合"},
    {"role": "assistant", "content": json.dumps({
        "members": ["rank(delta(close,20))", "rank(-delta(close,5))", "rank(-stddev(returns,20))"],
        "method": "lgbm", "train_frac": 0.6,
        "rationale": "中期动量+短期反转互补, 加低波动降回撤; 成员可能同源, lgbm 非线性去冗余。"},
        ensure_ascii=False)},
]


def _advise_messages(goal: str, repair: Optional[str] = None) -> List[dict]:
    msgs = [{"role": "system", "content": _ADVISOR_SYSTEM}] + _ADVISOR_FEWSHOT
    user = goal if not repair else f"{goal}\n\n上次配方有问题, 修正后重出: {repair}"
    return msgs + [{"role": "user", "content": user}]


def compose_advisor(goal: str, complete_fn: Optional[CompleteFn] = None) -> ComposeRecipe:
    """自然语言目标 → ComposeRecipe。校验成员 + 单轮 repair, 永不抛。"""
    goal = (goal or "").strip()
    if not goal:
        return ComposeRecipe(goal="", status="bad_output", error="缺少目标 (goal)")
    complete = complete_fn or _complete_json
    panel = _tiny_panel()
    repair: Optional[str] = None
    rec = ComposeRecipe(goal=goal)
    for _attempt in range(2):
        try:
            content = complete(_advise_messages(goal, repair))
        except Exception as e:
            return ComposeRecipe(goal=goal, status="llm_error",
                                 error=f"LLM 调用失败: {type(e).__name__}: {e}")
        try:
            obj = json.loads(content)
        except Exception as e:
            repair = f"输出非合法 JSON: {e}"
            rec.error = "LLM 输出无法解析为 JSON"
            continue
        members = [str(m).strip() for m in (obj.get("members") or []) if str(m).strip()]
        method = (obj.get("method") or "lgbm").strip()
        method = method if method in _METHODS else "lgbm"
        try:
            tf = float(obj.get("train_frac", 0.6))
        except (TypeError, ValueError):
            tf = 0.6
        tf = min(0.8, max(0.5, tf))
        rationale = (obj.get("rationale") or "").strip()
        if len(members) < 2:
            repair = "成员不足 2 个, 必须 >=2"
            rec.error = "成员不足 2 个"
            continue
        errs = [e for e in (_member_error(m, panel) for m in members) if e]
        if errs:
            repair = "; ".join(errs[:3])
            rec.error = "成员表达式有误: " + repair
            continue
        return ComposeRecipe(goal=goal, members=members, method=method,
                             train_frac=tf, rationale=rationale, status="ok")
    rec.status = "bad_output"
    return rec


def interpret_compose(result, complete_fn: Optional[CompleteFn] = None) -> str:
    """ComposeResult → 质性研判串。result 非 ok 或 LLM 失败 → 返回 "" (调用方回落机械 verdict)。"""
    if result is None or getattr(result, "status", "") != "ok":
        return ""
    complete = complete_fn or _complete_text
    comp = getattr(result, "composite", None)
    ic = getattr(comp, "ic", None)
    pf = getattr(comp, "portfolio", None)
    facts = {
        "method": result.method,
        "weights": result.weights,
        "members_oos": [{"name": m.name, "rank_ic": m.rank_ic, "sharpe": m.sharpe}
                        for m in result.member_oos],
        "composite_rank_ic": getattr(ic, "rank_ic_mean", None),
        "composite_sharpe": getattr(pf, "sharpe", None),
        "composite_ann": getattr(pf, "ann_return", None),
        "composite_mdd": getattr(pf, "max_drawdown", None),
        "n_train_dates": result.n_train_dates,
        "n_test_dates": result.n_test_dates,
        "verdict": result.verdict,
    }
    sys = ("你是量化组合研究员。基于给定 OOS 事实写 3-5 句中文研判: "
           "①综合分 vs 最优成员增益是否显著 ②权重是否过度集中(过拟合风险) "
           "③成员是否同源冗余 ④下一步迭代建议(换/加/正交化哪个)。只说数据支持的, 不编造数字。")
    msgs = [{"role": "system", "content": sys},
            {"role": "user", "content": json.dumps(facts, ensure_ascii=False, default=str)}]
    try:
        return (complete(msgs) or "").strip()
    except Exception:
        return ""
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python -m pytest tests/test_compose_llm.py -q`
Expected: PASS (8 passed)

- [ ] **Step 5: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/factors/compose/advisor.py tests/test_compose_llm.py
git -C G:\financial-analyst commit -m "feat(compose): LLM advisor (NL->recipe) + interpreter (OOS->research note)"
```

## Task 2: REST — advise 端点 + compose interpret

**Files:**
- Modify: `src/financial_analyst/buddy/server.py` (`ComposeReq` `:89`; `factor_compose_ep` `:1143`; 加 `AdviseReq` + advise 端点)
- Test: `tests/test_compose_llm.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_compose_llm.py`:

```python
from fastapi.testclient import TestClient
from financial_analyst.buddy.server import build_app


def test_rest_compose_advise(monkeypatch):
    rec = ComposeRecipe(goal="低回撤", members=["rank(-delta(close,5))", "rank(close)"],
                        method="linear", train_frac=0.6, rationale="反转+价位", status="ok")
    monkeypatch.setattr("financial_analyst.factors.compose.advisor.compose_advisor",
                        lambda goal, **kw: rec)
    client = TestClient(build_app())
    r = client.post("/factor/compose/advise", json={"goal": "低回撤"})
    assert r.status_code == 200
    body = r.json()
    assert body["members"] == ["rank(-delta(close,5))", "rank(close)"]
    assert body["method"] == "linear" and body["status"] == "ok"


def test_rest_compose_interpret(monkeypatch):
    from financial_analyst.factors.compose.compose import ComposeResult
    fake = ComposeResult(method="equal", members=["a", "b"], weights={"a": 0.5, "b": 0.5},
                         train_frac=0.6, n_train_dates=20, n_test_dates=12,
                         composite=None, member_oos=[], verdict="增益", status="ok")
    monkeypatch.setattr("financial_analyst.factors.compose.compose_factors",
                        lambda *a, **k: fake)
    monkeypatch.setattr("financial_analyst.factors.compose.advisor.interpret_compose",
                        lambda res, **kw: "这是 LLM 研判")
    client = TestClient(build_app())
    r = client.post("/factor/compose", json={
        "members": ["a", "b"], "method": "equal", "interpret": True})
    assert r.status_code == 200
    assert r.json().get("interpretation") == "这是 LLM 研判"
    # interpret=false → 不调 LLM, 无 interpretation
    r2 = client.post("/factor/compose", json={"members": ["a", "b"], "method": "equal"})
    assert not r2.json().get("interpretation")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_compose_llm.py::test_rest_compose_advise tests/test_compose_llm.py::test_rest_compose_interpret -q`
Expected: FAIL (advise 404; interpret 无该字段)

- [ ] **Step 3: `ComposeReq` 加 interpret + 加 `AdviseReq`**

`server.py` `ComposeReq` (`:89-96`, 以 `note: str = ""` 结束的那个) 末尾加 `interpret`; 其后加 `AdviseReq`:

```python
class ComposeReq(BaseModel):
    members: list
    method: str = "lgbm"
    universe: str = "csi300_active"
    freq: str = "month"
    train_frac: float = 0.6
    archive: bool = True
    note: str = ""
    interpret: bool = False


class AdviseReq(BaseModel):
    goal: str
    universe: str = "csi300_active"
```

- [ ] **Step 4: `factor_compose_ep` 改 sync def + interpret; 加 advise 端点**

把 `factor_compose_ep` (`:1143`) 整个替换 (async→sync def, 加 interpret), 并在其后加 advise 端点:

```python
    def factor_compose_ep(req: ComposeReq):
        """多因子合成: N(>=2) 个成员 → 综合分, OOS 评测 + 成员对比 → verdict。

        ``method``: equal / ic_weighted / linear / lgbm。成员数 <2 → 400。
        interpret=True 时附 LLM 研判 (interpret_compose, 用 asyncio.run 故端点为 sync def)。
        """
        if len(req.members) < 2:
            return JSONResponse(
                status_code=400,
                content={"error": "members 至少 2 个", "status": "too_few_factors"},
            )
        try:
            from financial_analyst.factors.eval import EvalConfig
            from financial_analyst.factors import compose as _compose_mod
            from financial_analyst.factors.compose import advisor as _advisor_mod
            cfg = EvalConfig(universe=req.universe, freq=req.freq)
            res = _compose_mod.compose_factors(
                req.members, cfg, method=req.method, train_frac=req.train_frac)
            if req.archive and getattr(res, "status", "") == "ok":
                try:
                    from financial_analyst.factors.research import (
                        ResearchArchive, record_from_compose)
                    ResearchArchive().append(record_from_compose(res, note=req.note))
                except Exception:
                    pass
            body = _jsonable(_asdict(res))
            if req.interpret and getattr(res, "status", "") == "ok":
                try:
                    body["interpretation"] = _advisor_mod.interpret_compose(res)
                except Exception:
                    body["interpretation"] = ""
            return body
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )

    @app.post("/factor/compose/advise")
    def factor_compose_advise_ep(req: AdviseReq):
        """输入顾问: 自然语言目标 → 合成配方 (成员表达式 + 方法 + 理由)。

        sync def — compose_advisor 用 asyncio.run, 须脱离事件循环 (同 forge 端点)。
        """
        try:
            from financial_analyst.factors.compose import advisor as _advisor_mod
            rec = _advisor_mod.compose_advisor(req.goal)
            return _jsonable(_asdict(rec))
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content={"error": f"{type(exc).__name__}: {exc}"},
            )
```

(注: `factor_compose_ep` 去掉 `async`; advise 端点也是 sync `def` — 两者都因 `interpret_compose`/`compose_advisor` 内部 `asyncio.run` 必须脱离请求事件循环, 同 SP-B.2 forge 端点教训。`_asdict` 已在文件内 import。)

- [ ] **Step 5: 跑测试确认通过 + compose REST 不回归**

Run: `python -m pytest tests/test_compose_llm.py tests/test_factor_rest.py -q`
Expected: PASS (新 advise/interpret 测试 + C.1/B.2 现有 REST 测试全绿; 含原 `test_compose_endpoint_ok`/`test_compose_archive_*` —— sync def 不影响 TestClient)

- [ ] **Step 6: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/buddy/server.py tests/test_compose_llm.py
git -C G:\financial-analyst commit -m "feat(rest): POST /factor/compose/advise + interpret flag on /factor/compose"
```

## Task 3: agent 工具 `factor_compose` 加 goal + 自动研判

**Files:**
- Modify: `src/financial_analyst/buddy/tools.py` (`_tool_factor_compose` `:1539`; 注册 input_schema `:2140`)
- Test: `tests/test_compose_llm.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_compose_llm.py`:

```python
def test_tool_factor_compose_goal_and_interpret(monkeypatch):
    from financial_analyst.buddy import tools as T
    from financial_analyst.factors.compose.compose import ComposeResult
    fake = ComposeResult(method="lgbm", members=["rank(close)", "rank(volume)"],
                         weights={"rank(close)": 0.6, "rank(volume)": 0.4},
                         train_frac=0.6, n_train_dates=20, n_test_dates=12,
                         composite=None, member_oos=[], verdict="增益", status="ok")
    rec = ComposeRecipe(goal="动量", members=["rank(close)", "rank(volume)"],
                        method="lgbm", train_frac=0.6, rationale="r", status="ok")
    monkeypatch.setattr("financial_analyst.factors.compose.advisor.compose_advisor",
                        lambda goal, **kw: rec)
    monkeypatch.setattr("financial_analyst.factors.compose.compose_factors",
                        lambda *a, **k: fake)
    monkeypatch.setattr("financial_analyst.factors.compose.advisor.interpret_compose",
                        lambda res, **kw: "LLM 研判: 注意过拟合")
    # goal 路径 (无 members) → advise 出成员 → 跑 → 附研判
    res = T._tool_factor_compose(members=None, goal="动量")
    assert not res.is_error
    assert "LLM 研判" in res.content
    assert "rank(close)" in res.content
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python -m pytest tests/test_compose_llm.py::test_tool_factor_compose_goal_and_interpret -q`
Expected: FAIL (`_tool_factor_compose` 不接受 `goal`)

- [ ] **Step 3: 改 `_tool_factor_compose` 签名 + goal 解析 + 研判**

把 `_tool_factor_compose` 签名 (`:1539`) 与 members 归一化段改为支持 goal; 并在 return 前附研判。

签名 (`:1539-1543`) 改为 (加 `goal`):

```python
def _tool_factor_compose(members=None, method: str = "lgbm",
                         universe: str = "csi300_active", freq: str = "month",
                         since: str = None, until: str = None,
                         train_frac: float = 0.6, goal: str = "",
                         archive: bool = False, note: str = "") -> ToolResult:
```

members 归一化段 (`:1551-1559`, `if isinstance(members, str)` 到 `is_error=True,)` 那个 if 块) 之前插入 goal 解析:

```python
    # 0) goal 给了 → 先 LLM 顾问出配方 (成员/方法)。
    goal = (goal or "").strip()
    if goal:
        from financial_analyst.factors.compose import advisor as _advisor_mod
        rec = _advisor_mod.compose_advisor(goal)
        if rec.status != "ok":
            return ToolResult(f"配方生成失败 (status={rec.status}): {rec.error}", is_error=True)
        members, method, train_frac = rec.members, rec.method, rec.train_frac
```

return 前 (`:1629` `return ToolResult("\n".join(lines))` 之前) 加研判:

```python
    try:
        from financial_analyst.factors.compose import advisor as _advisor_mod
        note_txt = _advisor_mod.interpret_compose(res)
        if note_txt:
            lines += ["", "## LLM 研判", note_txt]
    except Exception:
        pass
    return ToolResult("\n".join(lines))
```

- [ ] **Step 4: 注册表加 goal + members 非必填**

`factor_compose` Tool 的 `input_schema` (`:2140-2163`): `properties` 加 `goal`, `required` 去掉 members (改空 required, goal/members 二选一由工具体校验)。把 `properties` 里 members 之后加:

```python
                "goal": {"type": "string",
                         "description": "自然语言目标 (如 '低回撤动量反转组合'); 给了就让 LLM 顾问自动配成员+方法, 可不传 members"},
```

并把 `"required": ["members"],` 改为 `"required": [],` (goal 或 members 二选一, 工具体内校验)。description 末尾追加: `也可只给 goal 让 LLM 配方; 跑完自动附 LLM 研判。`

- [ ] **Step 5: 跑测试确认通过 + 工具/mcp 不回归**

Run: `python -m pytest tests/test_compose_llm.py tests/test_factor_compose_tool.py tests/test_mcp_server.py -q`
Expected: PASS (goal+研判测试 + 现有 compose 工具/mcp 测试全绿)

- [ ] **Step 6: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/buddy/tools.py tests/test_compose_llm.py
git -C G:\financial-analyst commit -m "feat(buddy): factor_compose accepts goal (LLM recipe) + auto LLM research note"
```

## Task 4: UI ComposeMode — 一句话配方 + 研判 panel

**Files:**
- Modify: `src/financial_analyst/ui/quant.jsx` (`ComposeMode`)
- Modify: `src/financial_analyst/ui/quant.html` (bump `?v=`)

> 编译校验: `node -e "const B=require('@babel/standalone'),fs=require('fs');B.transform(fs.readFileSync('quant.jsx','utf8'),{presets:['env','react']});console.log('OK')"` (在 `%TEMP%\fa_babel_check` 里跑, 传绝对路径; 见 SP-C 计划)。浏览器实测对 stub backend (canned advise/interpret)。

- [ ] **Step 1: ComposeMode 加 advise state + 一句话配方输入**

在 `ComposeMode` 的 state 区 (`const comp = useAsync();` 附近) 加:

```javascript
  const [goal, setGoal] = useState('');
  const advise = useAsync();
  const [recipeNote, setRecipeNote] = useState('');
  const doAdvise = () => {
    if (!goal.trim()) return;
    advise.run(() => postJSON('/factor/compose/advise', { goal, universe: poolParam(pool) }).then(rec => {
      if (rec && rec.status === 'ok') {
        setMembers(rec.members || []);
        if (rec.method) setMethod(rec.method);
        if (rec.train_frac) setTrainFrac(rec.train_frac);
        setRecipeNote(rec.rationale || '');
      } else {
        setRecipeNote('配方生成失败: ' + ((rec && rec.error) || advise.error || '未知'));
      }
      return rec;
    }));
  };
```

- [ ] **Step 2: 渲染配方输入条 (members chips 之上)**

在 `ComposeMode` return 的最上方 (members 输入条 `<div style={{ display: 'flex', gap: 8, flexWrap: 'wrap'...` 之前) 插入:

```javascript
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 10, flexWrap: 'wrap' }}>
        <input value={goal} onChange={e => setGoal(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') doAdvise(); }}
          placeholder="🪄 一句话配方: 如 低回撤的动量+反转组合"
          style={{ flex: '1 1 320px', padding: '6px 10px', border: '1px solid var(--jin)', fontFamily: 'var(--sans)', fontSize: 13, background: 'var(--paper)' }} />
        <button onClick={doAdvise} disabled={advise.loading} className="hover-pill"
          style={{ padding: '6px 14px', border: 'none', background: 'var(--jin)', color: 'var(--paper)', fontFamily: 'var(--serif)', fontSize: 12, cursor: 'pointer' }}>
          {advise.loading ? '配方中…' : 'LLM 配方 🪄'}
        </button>
      </div>
      {advise.error && <div style={{ marginBottom: 10 }}><ErrorBox error={advise.error} /></div>}
      {recipeNote && <div className="serif" style={{ fontSize: 12, color: 'var(--ink-2)', marginBottom: 10, paddingLeft: 10, borderLeft: '2px solid var(--jin)' }}>{recipeNote}</div>}
```

- [ ] **Step 3: compose 请求带 interpret + 渲染研判 panel**

把 `run` 里的 postJSON 改为带 `interpret: true`:

```javascript
  const run = () => { if (members.length < 2) return; comp.run(() => postJSON('/factor/compose', { members, method, universe: poolParam(pool), train_frac: trainFrac, interpret: true })); };
```

在结果区 `<FactorReportView report={res.composite} />` 之后 (composite 那个 `<div>` 块内末尾) 加研判 panel:

```javascript
          {res.interpretation && (
            <Panel title={<span>LLM 研判 <span className="mono" style={{ fontSize: 9, color: 'var(--ink-3)', marginLeft: 6 }}>compose interpreter</span></span>}>
              <div className="serif" style={{ fontSize: 13, color: 'var(--ink-1)', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>{res.interpretation}</div>
            </Panel>
          )}
```

- [ ] **Step 4: 编译校验**

Run (在 `%TEMP%\fa_babel_check`): `node -e "const B=require('@babel/standalone'),fs=require('fs');B.transform(fs.readFileSync('G:/financial-analyst/src/financial_analyst/ui/quant.jsx','utf8'),{presets:['env','react']});console.log('OK')"`
Expected: `OK`

- [ ] **Step 5: bump cache-buster**

`quant.html` 的 `quant.jsx?v=20260529-7` → `-8`。

- [ ] **Step 6: 浏览器实测 (stub backend 加 canned advise/interpret)**

在 `stub_serve.py` 加 canned advisor/interpret monkeypatch (重启 stub): `import financial_analyst.factors.compose.advisor as _A; _A.compose_advisor = lambda goal, **k: _A.ComposeRecipe(goal=goal, members=["rank(-delta(close,5))","rank(close)"], method="linear", train_frac=0.6, rationale="反转+价位互补 (canned)", status="ok"); _A.interpret_compose = lambda res, **k: "综合分相对最优成员略增益; lgbm 权重略集中, 留意过拟合; 建议加一个低相关的量价因子。(canned)"`。
浏览器 `/quant.html` 多因子合成: 输目标 →「LLM 配方」→ 成员/方法自动填 + 理由显示 → 合成评测 → 结果下方出「LLM 研判」panel。

- [ ] **Step 7: 提交**

```bash
git -C G:\financial-analyst add src/financial_analyst/ui/quant.jsx src/financial_analyst/ui/quant.html
git -C G:\financial-analyst commit -m "feat(ui): ComposeMode one-line recipe (advise) + LLM research note panel"
```

## Task 5: 全量回归

- [ ] **Step 1: 全量后端回归 (控制端 miniconda)**

Run: `python -m pytest tests/ -q`
Expected: 无新增失败 (基线 954 passed/1 skip → 现 ~965 passed; test_compose_llm 全绿, compose/forge/rest 不回归)。

- [ ] **Step 2: 两 jsx 编译校验**

Run: babel transform `quant.jsx` + `app.jsx` → 均 `OK`。

- [ ] **Step 3: 终审自检**

- `compose_factors` 引擎/verdict 未改 (git diff 确认只动 advisor.py + 接入点)。
- advise 端点 + compose interpret + 工具 goal 三处可达。
- `git status` 工作区干净 (除会话前已存在的未跟踪项)。

- [ ] **Step 4: 最终提交 (如有零散改动)**

```bash
git -C G:\financial-analyst add -A
git -C G:\financial-analyst commit -m "test(compose-llm): full regression green for SP-D.2"
```

---

## Self-Review (作者已过一遍)

**Spec 覆盖:** compose_advisor (T1) ✓; interpret_compose (T1) ✓; 校验+repair (T1 测 test_compose_advisor_repairs_bad_member) ✓; 永不抛 4 状态 (T1 bad_output/llm_error) ✓; REST advise + interpret 字段 + sync def (T2) ✓; agent 工具 goal + 自动研判 (T3) ✓; UI 一句话配方 + 研判 panel (T4) ✓; 引擎不碰 LLM (advisor.py 独立, compose_factors 不改) ✓; 测试 stub complete_fn (T1-T3) ✓。

**类型一致:** `ComposeRecipe{goal,members,method,train_frac,rationale,status,error}` T1 定义, T2/T3 按名引用一致; `compose_advisor(goal, complete_fn)` / `interpret_compose(result, complete_fn)` 签名跨任务一致; 端点/工具经 `_advisor_mod.compose_advisor`/`_advisor_mod.interpret_compose` 模块属性访问 (T2/T3 monkeypatch 同路径) 一致; `CompleteFn = Callable[[List[dict]], str]`; ComposeResult 复用 (不改字段)。

**已知简化:** advisor 出表达式不从库名挑; 机械 verdict 保留 (研判是其上 LLM 层); UI 研判随 interpret=true 自动出 (无单独按钮)。

**环境备注:** 真 LLM 这台机 qwen 直连可用但延迟抖动 → 单测全用注入 stub complete_fn (不真调); 浏览器实测用 stub_serve 里 canned advise/interpret。
