# 帷幄能力扩展实现计划(A/B/C 三阶段)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 给帷幄(观澜 console agent)补齐工具入口——能把分析好的因子持久化入库、能合成/物化特征/查 DSL 字段词表、能调一批被网关挡掉的研究/数据引擎工具、能自省自己有哪些工具与后端能力。

**Architecture:** 全部改动落 `guanlan_v2/console/`(`tools.py` 新增 `*_impl` + specs + `CONSOLE_ALLOWED`;`api.py` 加 ETF 研报后台跑道 + 系统提示词),不动 `engine/`。新 `ww_` 工具经 `_wrap(impl)` 进 `TOOL_REGISTRY` 并加入 `CONSOLE_ALLOWED`;放行已注册引擎工具只加 `CONSOLE_ALLOWED`;薄包装经 `get_tool(name).run()` 代理引擎工具,只在 ww_ 层加确认门。

**Tech Stack:** Python 3.13、FastAPI、pytest、引擎 `financial_analyst.buddy`(`Tool`/`ToolResult`/`get_tool`/`TOOL_REGISTRY`)、guanlan 自有 `/factorlib`、`/workflow`、`/feature` 端点。

**关联 spec:** `docs/superpowers/specs/2026-06-16-weiwo-capability-expansion-design.md`

**重要约定(本仓):**
- **不是 git 仓库**(`Is a git repository: false`)→ 计划中以「运行全量 pytest 当 checkpoint」替代 `git commit`,**不写 commit 步骤**。
- **GateGuard**:每个文件首次编辑前须先报 facts(谁调用/无重复/数据 schema/用户指令)再 Edit。
- **改后端生效**:杀 9999 监听 PID 等端口释放(防 10048),看门狗 8s 自动拉新代码;真机验证用 deepseek 走 `/console/send`。
- 测试运行:`G:/financial-analyst/.venv/Scripts/python.exe -m pytest`(子进程守护测试须能 import 在仓 `engine/`,见现有 `tests/conftest.py`)。

**计数基线(守护测试 `test_engine_profile_excludes_ww_but_console_whitelist_resolves` 须随每阶段更新):**
| 阶段后 | registered_ww | console_n | explicit_ww_n |
|---|---|---|---|
| 现状 | 17 | 24 | 17 |
| A 完成 | 20 | 38 | 20 |
| B 完成 | 24 | 42 | 24 |
| C 完成 | 26 | 44 | 26 |

(A 加 3 个 ww_ + 11 个白名单引擎工具;B 加 4 个 ww_;C 加 2 个 ww_。7 个原 buddy 白名单工具不变。)

---

## 文件结构

| 文件 | 职责 | 改动 |
|---|---|---|
| `guanlan_v2/console/tools.py` | 全部 `*_impl`、specs 注册、`CONSOLE_ALLOWED` | 主改:9 个新 impl + 9 条 specs + 白名单扩充 |
| `guanlan_v2/console/api.py` | 系统提示词、ETF 研报后台跑道 | 加 `_run_etf_report_bg` + `_spawn_bg` 分发 + 提示词 |
| `tests/test_console_tools.py` | impl 纯逻辑单测 + 守护计数 | 加各新 impl 测试 + 更新计数 |
| `tests/test_console_api.py` | ETF 后台跑道行为 | 加 B4 跑道测试 |

---

# Phase A — 接通已有能力

## Task A1: `ww_factorlib_save` impl(因子入库,confirm)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(新增 `factorlib_save_impl`,放在 `f10_impl` 之后、`_read_memory_file` 之前)
- Test: `tests/test_console_tools.py`

- [ ] **Step 1: 写失败测试**

加到 `tests/test_console_tools.py`:

```python
def test_factorlib_save_impl_posts_and_reports_registered(monkeypatch):
    sent = {}
    def fake_post(path, payload, timeout=120):
        sent["path"] = path; sent.update(payload)
        return {"ok": True, "name": payload["name"], "expr": payload["expr"],
                "family": "library_mined", "file": "x.json", "registered": True}
    monkeypatch.setattr(ct, "_self_post", fake_post)
    res = ct.factorlib_save_impl(name="my_mom", expr="rank(-delta(close,20))")
    assert sent["path"] == "/factorlib/save"
    assert sent["name"] == "my_mom" and sent["expr"] == "rank(-delta(close,20))"
    assert sent["source"] == "帷幄 · ww_factorlib_save"
    assert res["ok"] is True and "已注册" in res["content"]
    assert res["artifact"]["page"] == "factor"


def test_factorlib_save_impl_saved_but_not_registered_is_honest(monkeypatch):
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120: {
        "ok": True, "name": "x", "expr": "rank(roe)", "registered": False,
        "reason": "RuntimeError: frozen"})
    res = ct.factorlib_save_impl(name="x", expr="rank(roe)")
    assert res["ok"] is True and "落盘成功" in res["content"] and "未注册" in res["content"]


def test_factorlib_save_impl_rejects_empty():
    assert ct.factorlib_save_impl(name="", expr="rank(roe)")["ok"] is False
    assert ct.factorlib_save_impl(name="x", expr="")["ok"] is False


def test_factorlib_save_impl_backend_failure_passthrough(monkeypatch):
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120: {
        "ok": False, "reason": "因子名已存在: my_mom"})
    res = ct.factorlib_save_impl(name="my_mom", expr="rank(roe)")
    assert res["ok"] is False and "因子名已存在" in res["content"]
```

- [ ] **Step 2: 运行验证失败**

Run: `python -m pytest tests/test_console_tools.py -k factorlib_save -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'factorlib_save_impl'`)

- [ ] **Step 3: 写最小实现**

加到 `guanlan_v2/console/tools.py`(`f10_impl` 之后):

```python
def factorlib_save_impl(name: str = "", expr: str = "", family: str = "library_mined",
                        description: str = "", is_qlib: bool = False) -> Dict[str, Any]:
    """把一条因子表达式存入 guanlan 因子库 mined/ 并运行期注册进 zoo registry。
    透传后端 /factorlib/save(校验 validate_expr+compile_factor → 重名拒绝 → 落盘 → register)。
    诚实:落盘成功即 ok:True,运行期注册是否生效看 registered;非法/重名 → 后端 ok:False 原样回。
    """
    nm = (name or "").strip()
    ex = (expr or "").strip()
    if not nm:
        return {"ok": False, "content": "缺少因子名 name", "artifact": None}
    if not ex:
        return {"ok": False, "content": "缺少因子表达式 expr", "artifact": None}
    try:
        r = _self_post("/factorlib/save", {"name": nm, "expr": ex, "family": family or "library_mined",
                                           "description": description or "", "is_qlib": bool(is_qlib),
                                           "source": "帷幄 · ww_factorlib_save"})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"因子入库调用失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"因子入库失败: {r.get('reason', '未知原因')}", "artifact": None}
    registered = bool(r.get("registered"))
    zoo = r.get("expr", ex)
    if registered:
        msg = f"因子已入库并注册:「{nm}」= {zoo}(已注册进 zoo,可被选股/工作流复用)。"
    else:
        msg = (f"因子已入库(落盘成功):「{nm}」= {zoo},但运行期未注册"
               f"({r.get('reason', '原因未知')})——重启后随库加载或核对后重试。")
    return {"ok": True, "content": msg,
            "artifact": artifact("factor_saved", page="factor", channel="workflow",
                                 payload={"name": nm, "expr": zoo, "registered": registered})}
```

- [ ] **Step 4: 运行验证通过**

Run: `python -m pytest tests/test_console_tools.py -k factorlib_save -v`
Expected: PASS（4 passed）

- [ ] **Step 5: Checkpoint**

Run: `python -m pytest tests/test_console_tools.py -q`
Expected: 全绿（注册/计数测试此刻仍是旧值,A-final 再更新）

---

## Task A2: `ww_update_data` / `ww_news_collect` 薄包装 impl(confirm)

**Files:**
- Modify: `guanlan_v2/console/tools.py`(新增 `_proxy_engine_tool`、`update_data_impl`、`news_collect_impl`)
- Test: `tests/test_console_tools.py`

- [ ] **Step 1: 写失败测试**

```python
def test_update_data_impl_proxies_engine_tool(monkeypatch):
    import types
    calls = {}
    class _TR:
        def __init__(self, content, is_error=False, side_effect=None):
            self.content = content; self.is_error = is_error; self.side_effect = side_effect
    def fake_get_tool(name):
        calls["name"] = name
        return types.SimpleNamespace(run=lambda **kw: (calls.update(kw) or _TR("更新完成: 300 只")))
    fake_mod = types.SimpleNamespace(get_tool=fake_get_tool, ToolResult=_TR)
    monkeypatch.setattr(ct, "_buddy_tools_mod", lambda: fake_mod)
    res = ct.update_data_impl(codes="SZ300750", mode="quick")
    assert calls["name"] == "update_data" and calls["codes"] == "SZ300750" and calls["mode"] == "quick"
    assert res["ok"] is True and "更新完成" in res["content"]


def test_update_data_impl_tool_missing_is_honest(monkeypatch):
    import types
    monkeypatch.setattr(ct, "_buddy_tools_mod",
                        lambda: types.SimpleNamespace(get_tool=lambda n: None))
    res = ct.update_data_impl()
    assert res["ok"] is False and "不可用" in res["content"]


def test_news_collect_impl_proxies_engine_tool(monkeypatch):
    import types
    calls = {}
    class _TR:
        def __init__(self, content, is_error=False, side_effect=None):
            self.content = content; self.is_error = is_error
    def fake_get_tool(name):
        calls["name"] = name
        return types.SimpleNamespace(run=lambda **kw: (calls.update(kw) or _TR("入库 50 条")))
    monkeypatch.setattr(ct, "_buddy_tools_mod",
                        lambda: types.SimpleNamespace(get_tool=fake_get_tool, ToolResult=_TR))
    res = ct.news_collect_impl(sources="kuaixun,longhu", limit=100)
    assert calls["name"] == "news_collect" and calls["sources"] == "kuaixun,longhu"
    assert res["ok"] is True and "入库" in res["content"]
```

- [ ] **Step 2: 运行验证失败**

Run: `python -m pytest tests/test_console_tools.py -k "update_data_impl or news_collect_impl" -v`
Expected: FAIL（`has no attribute 'update_data_impl'`）

- [ ] **Step 3: 写最小实现**

加到 `guanlan_v2/console/tools.py`（紧接 `factorlib_save_impl`）:

```python
def _proxy_engine_tool(tool_name: str, fail_label: str, **kw: Any) -> Dict[str, Any]:
    """薄包装:代理执行一个已注册的引擎工具(返回其 ToolResult 的 content/is_error)。
    在 ww_ 层加确认门(specs 里 confirm_required=True),引擎工具本身不进白名单。"""
    try:
        bt = _buddy_tools_mod()
        tool = bt.get_tool(tool_name)
        if tool is None:
            return {"ok": False, "content": f"引擎 {tool_name} 工具不可用(未注册)", "artifact": None}
        res = tool.run(**{k: v for k, v in kw.items() if v is not None})
        return {"ok": not getattr(res, "is_error", False),
                "content": str(getattr(res, "content", "")), "artifact": None}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"{fail_label}: {e}", "artifact": None}


def update_data_impl(codes: Optional[str] = None, mode: str = "quick") -> Dict[str, Any]:
    """增量更新行情数据(代理引擎 update_data;ww_ 层确认门防误触 all 全市场重拉)。"""
    return _proxy_engine_tool("update_data", "数据更新调用失败", codes=codes, mode=mode or "quick")


def news_collect_impl(sources: str = "kuaixun,longhu,sinafinance",
                      limit: int = 200, code: Optional[str] = None) -> Dict[str, Any]:
    """从上游抓新闻入本地库(代理引擎 news_collect;ww_ 层确认门)。"""
    return _proxy_engine_tool("news_collect", "新闻抓取调用失败",
                              sources=sources or "kuaixun,longhu,sinafinance",
                              limit=int(limit or 200), code=code)
```

- [ ] **Step 4: 运行验证通过**

Run: `python -m pytest tests/test_console_tools.py -k "update_data_impl or news_collect_impl" -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Checkpoint**

Run: `python -m pytest tests/test_console_tools.py -q`
Expected: 全绿（计数测试仍旧值）

---

## Task A3: 注册 A 阶段工具 + 扩 CONSOLE_ALLOWED + 系统提示词 + 守护计数

**Files:**
- Modify: `guanlan_v2/console/tools.py`（`register_console_tools` specs + `CONSOLE_ALLOWED`）
- Modify: `guanlan_v2/console/api.py`（`_SYSTEM_PROMPT`）
- Modify: `tests/test_console_tools.py`（守护计数）

- [ ] **Step 1: 在 `register_console_tools` 的 `specs` 列表末尾(`ww_f10` 之后)追加 3 条**

```python
        ("ww_factorlib_save",
         "把一条因子表达式存进因子库并注册进引擎(校验+落盘 mined/+运行期注册→选股/工作流可复用)。"
         "用户说『把这条因子存下来/入库/沉淀成因子』时用。需用户确认。is_qlib=true 则先把 Qlib 形($close/Ref/Std)译成 zoo。",
         {"type": "object", "properties": {
             "name": {"type": "string", "description": "因子名(唯一,重名后端拒绝覆盖)"},
             "expr": {"type": "string", "description": "zoo 因子表达式,如 rank(-delta(close,20));is_qlib=true 时填 Qlib 形"},
             "family": {"type": "string", "default": "library_mined"},
             "description": {"type": "string"},
             "is_qlib": {"type": "boolean", "default": False}},
          "required": ["name", "expr"]},
         _wrap(factorlib_save_impl), "seconds", True),
        ("ww_update_data",
         "增量更新行情数据(pytdx+腾讯,quick只日线/full含5min+daily_basic)。用户说『更新数据/拉最新/同步行情』时用。"
         "需用户确认(codes=all 是全市场 5-10 分钟重拉)。",
         {"type": "object", "properties": {
             "codes": {"type": "string", "description": "逗号分隔代码 SH600519,SZ300750;all=全市场(慎用);省略=全部 instruments"},
             "mode": {"type": "string", "enum": ["quick", "full"], "default": "quick"}}},
         _wrap(update_data_impl), "seconds", True),
        ("ww_news_collect",
         "从上游抓最新新闻入本地库(快讯/龙虎榜/新浪/雪球情绪等)。news_query 查空或要最新时用。需用户确认。",
         {"type": "object", "properties": {
             "sources": {"type": "string", "default": "kuaixun,longhu,sinafinance",
                         "description": "逗号分隔源:kuaixun,longhu,sinafinance,shareholders,ths-hot(公开);xueqiu-*(需cookie)"},
             "limit": {"type": "integer", "default": 200},
             "code": {"type": "string", "description": "仅 xueqiu-comments 个股情绪需要"}}},
         _wrap(news_collect_impl), "seconds", True),
```

- [ ] **Step 2: 扩 `CONSOLE_ALLOWED`**

把 `CONSOLE_ALLOWED` 改为(新增 3 个 ww_ + 11 个只读引擎工具):

```python
CONSOLE_ALLOWED = {
    "ww_plan_update", "ww_factor_analyze", "ww_backtest", "ww_screen_run", "ww_screen_factors",
    "ww_seats_decide", "ww_seats_bind", "ww_cards_query", "ww_reports_query",
    "ww_report_run", "ww_show_page", "ww_cards_save", "ww_memory_write", "ww_memory_read",
    "ww_seats_history", "ww_news_search", "ww_f10",
    "ww_factorlib_save", "ww_update_data", "ww_news_collect",          # A 新增 ww_
    "quote_lookup", "realtime_quote", "stock_brief", "financials",
    "news_query", "wisdom_search", "quant_reports",
    # A 新增:直接放行的只读引擎研究工具(已注册,只缺白名单)
    "iwencai_search", "ths_fund_flow", "fund_flow_change", "ths_concept_board",
    "market_status", "mainline_radar", "overseas_radar", "morning_brief",
    "quote_batch", "chain_for", "industry_show",
}
```

- [ ] **Step 3: 更新 `_SYSTEM_PROMPT`（`guanlan_v2/console/api.py:26`）**

在「另有:…」段末尾追加(保留原有全部文字,只续写):

```
另有:因子入库 ww_factorlib_save(把分析好的 zoo 因子存进库并注册,需确认)、更新数据 ww_update_data(需确认)、抓新闻入库 ww_news_collect(需确认)、问财选股 iwencai_search(自然语言选股)、资金流 ths_fund_flow/fund_flow_change、概念板块 ths_concept_board、大盘状态 market_status、主线/海外雷达 mainline_radar/overseas_radar、晨报 morning_brief、批量行情 quote_batch、产业链 chain_for、行业 industry_show。
```

并在纪律区追加一条:

```
9. 分析出一条好因子(ww_factor_analyze IC 不错)且用户认可后,可用 ww_factorlib_save 把它入库(需确认),之后能在 ww_screen_run / 工作流里按 id 复用。
```

- [ ] **Step 4: 更新守护计数测试（`tests/test_console_tools.py` 的 `test_engine_profile_excludes_ww_but_console_whitelist_resolves`）**

把三处断言改为:

```python
    assert len(out["registered_ww"]) == 20                    # A 后:17 + 3
    ...
    assert out["console_n"] == 38 and out["console_missing"] == []
    assert out["explicit_n"] == 38 and out["explicit_ww_n"] == 20
```

（`test_register_console_tools_idempotent` 保持子集断言即可,无需改。）

- [ ] **Step 5: 运行全量验证**

Run: `python -m pytest tests/test_console_tools.py tests/test_console_api.py -q`
Expected: 全绿（守护计数 = 20/38/38/20；`console_missing == []` 证明 11 个引擎工具确实已注册可解析）

- [ ] **Step 6: 真机端到端验证(Phase A 验收)**

杀 9999 等看门狗拉新代码后,经 `/console/send` 发:
1. 「帮我分析 `rank(-delta(close,20))` 这条因子,IC 不错的话存进因子库叫 mom_rev_20」→ 观察:`ww_factor_analyze` 出真 RankIC → `ww_factorlib_save`(弹确认)→ 确认后回 `registered=True`。
2. 「ww_screen_factors 能查到 mom_rev_20 吗」/直接 `ww_screen_factors` → 确认新因子出现在目录(证明真入库并对 `/screen/run` 可见)。
3. 「问财:市盈率小于20且ROE大于15%的票」→ `iwencai_search` 真返回。
4. 「今天主力资金流榜」→ `ths_fund_flow` 真返回。
Expected: 四条均真实成功,入库因子可被选股目录查到。完成后删除 mom_rev_20 测试因子(`mined/mom_rev_20.json`)避免污染。

---

# Phase B — 因子炼制工作流

## Task B1: `ww_factor_compose` impl(多因子合成,免确认)

**Files:**
- Modify: `guanlan_v2/console/tools.py`（新增 `factor_compose_impl`）
- Test: `tests/test_console_tools.py`

- [ ] **Step 1: 写失败测试**

```python
def test_factor_compose_impl_posts_members(monkeypatch):
    sent = {}
    def fake_post(path, payload, timeout=120):
        sent["path"] = path; sent.update(payload)
        return {"ok": True, "headline_ic": {"rank_ic": 0.061, "rank_icir": 0.42},
                "weights": [{"name": "rank(roe)", "w": 0.5}, {"name": "mom_60", "w": 0.5}],
                "n_dates": 30}
    monkeypatch.setattr(ct, "_self_post", fake_post)
    res = ct.factor_compose_impl(members=["rank(roe)", "mom_60"], method="ic")
    assert sent["path"] == "/workflow/compose"
    assert sent["members"] == ["rank(roe)", "mom_60"] and sent["method"] == "ic"
    assert res["ok"] is True and "0.061" in res["content"]
    assert res["artifact"]["page"] == "factor"


def test_factor_compose_impl_needs_two_members():
    assert ct.factor_compose_impl(members=["rank(roe)"])["ok"] is False
    assert ct.factor_compose_impl(members=[])["ok"] is False


def test_factor_compose_impl_backend_fail(monkeypatch):
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120:
                        {"ok": False, "reason": "面板加载失败"})
    res = ct.factor_compose_impl(members=["a", "b"])
    assert res["ok"] is False and "面板加载失败" in res["content"]
```

- [ ] **Step 2: 运行验证失败**

Run: `python -m pytest tests/test_console_tools.py -k factor_compose -v`
Expected: FAIL

- [ ] **Step 3: 写最小实现**

```python
def factor_compose_impl(members: Optional[List[str]] = None, method: str = "equal",
                        universe: str = "csi300", oos_frac: float = 0.3) -> Dict[str, Any]:
    """多因子合成(equal/ic/icir 加权)→ OOS 报告 + 各腿权重(/workflow/compose)。只评测不入库。"""
    mem = [str(m).strip() for m in (members or []) if str(m).strip()]
    if len(mem) < 2:
        return {"ok": False, "content": "至少需要 2 个因子(members)才能合成", "artifact": None}
    if method not in {"equal", "ic", "icir"}:
        method = "equal"
    try:
        r = _self_post("/workflow/compose", {"members": mem, "method": method,
                                             "universe": universe, "oos_frac": oos_frac})
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"因子合成调用失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"因子合成失败: {r.get('reason', '未知原因')}", "artifact": None}
    h = r.get("headline_ic") or {}
    w = r.get("weights") or []
    wline = " · ".join(f"{x.get('name')}={x.get('w')}" for x in w[:6]) if w else ""
    return {"ok": True,
            "content": (f"合成完成({method}): RankIC {h.get('rank_ic')} · RankICIR {h.get('rank_icir')}"
                        f" · 期数 {r.get('n_dates')}" + (f"\n权重: {wline}" if wline else "")),
            "artifact": artifact("compose_report", page="factor", channel="workflow",
                                 payload={"members": mem, "method": method}),
            "raw": r}
```

- [ ] **Step 4: 运行验证通过**

Run: `python -m pytest tests/test_console_tools.py -k factor_compose -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_console_tools.py -q`（计数测试仍 A 值,B5 再更新）

---

## Task B2: `ww_feature_build` impl(物化特征,免确认)

**Files:**
- Modify: `guanlan_v2/console/tools.py`（新增 `feature_build_impl`）
- Test: `tests/test_console_tools.py`

- [ ] **Step 1: 写失败测试**

```python
def test_feature_build_impl_posts_features(monkeypatch):
    sent = {}
    def fake_post(path, payload, timeout=120):
        sent["path"] = path; sent.update(payload)
        return {"ok": True, "n_dates": 40, "n_codes": 300, "coverage": 0.93,
                "features": [{"name": "rank(roe)", "rank_ic": 0.04},
                             {"name": "mom_60", "rank_ic": 0.05}]}
    monkeypatch.setattr(ct, "_self_post", fake_post)
    res = ct.feature_build_impl(features=["rank(roe)", "mom_60"], fwd_days=10)
    assert sent["path"] == "/feature/build" and sent["features"] == ["rank(roe)", "mom_60"]
    assert sent["fwd_days"] == 10
    assert res["ok"] is True
    assert "300" in res["content"] and "0.04" in res["content"]


def test_feature_build_impl_needs_features():
    assert ct.feature_build_impl(features=[])["ok"] is False


def test_feature_build_impl_backend_fail(monkeypatch):
    monkeypatch.setattr(ct, "_self_post", lambda path, payload, timeout=120:
                        {"ok": False, "reason": "物化后全 NaN"})
    res = ct.feature_build_impl(features=["bad_field"])
    assert res["ok"] is False and "全 NaN" in res["content"]
```

- [ ] **Step 2: 运行验证失败** — `python -m pytest tests/test_console_tools.py -k feature_build -v`（FAIL）

- [ ] **Step 3: 写最小实现**

```python
def feature_build_impl(features: Optional[List[str]] = None, label: str = "",
                       fwd_days: int = 5, universe: str = "csi_fast",
                       oos_frac: float = 0.0) -> Dict[str, Any]:
    """物化特征工程(真 X/y)→ 真统计 + 逐特征 RankIC(/feature/build)。label 空=前向收益。"""
    feats = [str(f).strip() for f in (features or []) if str(f).strip()]
    if not feats:
        return {"ok": False, "content": "缺少特征表达式 features", "artifact": None}
    body: Dict[str, Any] = {"features": feats, "fwd_days": int(fwd_days or 5),
                            "universe": universe, "oos_frac": oos_frac}
    if (label or "").strip():
        body["label"] = label.strip()
    try:
        r = _self_post("/feature/build", body)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"特征物化调用失败: {e}", "artifact": None}
    if not r.get("ok"):
        return {"ok": False, "content": f"特征物化失败: {r.get('reason', '未知原因')}", "artifact": None}
    fs = r.get("features") or []
    ic_line = " · ".join(f"{x.get('name')} IC{float(x.get('rank_ic')):+.3f}"
                         for x in fs[:8] if x.get('rank_ic') is not None)
    return {"ok": True,
            "content": (f"特征物化完成: {r.get('n_codes')} 票 × {r.get('n_dates')} 期"
                        f" · 覆盖 {_pct(r.get('coverage'))}" + (f"\n逐特征 RankIC: {ic_line}" if ic_line else "")),
            "artifact": artifact("feature_matrix", page="factor", channel="workflow",
                                 payload={"features": feats}),
            "raw": r}
```

- [ ] **Step 4: 运行验证通过** — `python -m pytest tests/test_console_tools.py -k feature_build -v`（PASS）

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_console_tools.py -q`

---

## Task B3: `ww_factor_fields` impl(DSL 字段词表,免确认)

**Files:**
- Modify: `guanlan_v2/console/tools.py`（新增 `_FACTOR_FIELD_EXAMPLES`、`factor_fields_impl`）
- Test: `tests/test_console_tools.py`

- [ ] **Step 1: 写失败测试**

```python
def test_factor_fields_impl_lists_vocab():
    res = ct.factor_fields_impl()
    assert res["ok"] is True
    c = res["content"]
    assert "close" in c and "roe" in c and "rank" in c and "regbeta" in c
    assert "rank(" in c                       # 至少给一条范例
    assert "词表" in c or "DSL" in c            # 诚实口径
```

- [ ] **Step 2: 运行验证失败** — `python -m pytest tests/test_console_tools.py -k factor_fields -v`（FAIL）

- [ ] **Step 3: 写最小实现**

```python
_FACTOR_FIELD_EXAMPLES = (
    "rank(-delta(close,20))       动量反转(20日跌幅排名)",
    "-stddev(returns,20)          低波(20日收益波动取反)",
    "rank(roe)                    高 ROE",
    "rank(-amihud_20)             高流动性(Amihud 取反)",
    "regbeta(returns,idx_ret,60)  对大盘 60 日滚动 β(共振/跟随)",
)


def factor_fields_impl() -> Dict[str, Any]:
    """返回 zoo DSL 字段+算子词表 + 几条范例,供写因子表达式前查合法字段名(治猜错字段→validate 失败)。
    诚实:这是 DSL 词表(字段含中文名/方向/频率/口径),不是完整方向语义层。"""
    try:
        from financial_analyst.factors.zoo.expr import FACTOR_VOCAB
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"字段词表读取失败: {e}", "artifact": None}
    examples = "\n".join("  " + s for s in _FACTOR_FIELD_EXAMPLES)
    return {"ok": True,
            "content": ("zoo 因子 DSL 词表(写表达式只能用这些字段/算子,否则校验失败):\n"
                        + str(FACTOR_VOCAB) + "\n\n范例:\n" + examples),
            "artifact": None}
```

- [ ] **Step 4: 运行验证通过** — `python -m pytest tests/test_console_tools.py -k factor_fields -v`（PASS）

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_console_tools.py -q`

---

## Task B4: `ww_etf_report_run` + ETF 后台跑道

**Files:**
- Modify: `guanlan_v2/console/tools.py`（新增 `etf_report_run_impl`）
- Modify: `guanlan_v2/console/api.py`（`_spawn_bg` 分发 + 新增 `_run_etf_report_bg`）
- Test: `tests/test_console_tools.py`、`tests/test_console_api.py`

- [ ] **Step 1: 写 impl 失败测试（`tests/test_console_tools.py`）**

```python
def test_etf_report_run_impl_returns_background(monkeypatch):
    import types
    monkeypatch.setattr(ct, "_buddy_tools_mod",
                        lambda: types.SimpleNamespace(normalize_code=lambda c: "SH" + c))
    res = ct.etf_report_run_impl(code="510300", name="沪深300ETF")
    assert res["ok"] is True
    assert res["background"]["kind"] == "etf_report" and res["background"]["code"] == "SH510300"


def test_etf_report_run_impl_rejects_bad_code():
    assert ct.etf_report_run_impl(code="bad!")["ok"] is False
```

- [ ] **Step 2: 运行验证失败** — `python -m pytest tests/test_console_tools.py -k etf_report_run -v`（FAIL）

- [ ] **Step 3: 写 impl（`guanlan_v2/console/tools.py`,仿 `report_run_impl`）**

```python
def etf_report_run_impl(code: str, name: str = "", asof: Optional[str] = None) -> Dict[str, Any]:
    """受理 ETF 深度研报(后台跑引擎 run_etf_report,5-8 分钟,不阻塞)。返回 background 信封。"""
    code = (code or "").strip().upper()
    if _re.match(r"^\d{6}$", code):
        try:
            code = _buddy_tools_mod().normalize_code(code)
        except Exception:
            return {"ok": False, "content": f"无法规范化代码 {code}(需 SH/SZ 前缀)", "artifact": None}
    if not _CODE_RE.match(code):
        return {"ok": False, "content": f"代码格式非法: {code}(应为 SH510300 形)", "artifact": None}
    return {"ok": True,
            "content": f"ETF 研报已受理:{name or code} 后台生成中(约 5-8 分钟),完成后通知并可翻阅。",
            "artifact": None,
            "background": {"kind": "etf_report", "code": code, "name": name, "asof": (asof or None)}}
```

- [ ] **Step 4: 运行验证通过** — `python -m pytest tests/test_console_tools.py -k etf_report_run -v`（PASS）

- [ ] **Step 5: 在 `guanlan_v2/console/api.py` 加 ETF 分发 + 后台跑道**

5a. 在文件顶部(`_archive_research` 附近)定义可分发 kind 常量(供测试断言):

```python
_BG_KINDS = {"report", "etf_report"}
```

5b. 把 `_spawn_bg`（约 :369）的分发处:

```python
        if (spec or {}).get("kind") == "report":
            await _run_report_bg(sid, spec)
```

改为:

```python
        _k = (spec or {}).get("kind")
        if _k == "report":
            await _run_report_bg(sid, spec)
        elif _k == "etf_report":
            await _run_etf_report_bg(sid, spec)
```

5c. 与 `_run_report_bg` 同作用域新增 `_run_etf_report_bg`(结构镜像,但在 executor 跑引擎工具,不 shell CLI、不碰 `_call_buddy_report`、不去重/搭车):

```python
    async def _run_etf_report_bg(sid: str, spec: Dict[str, Any]):
        """ETF 研报后台跑道:在 executor 跑引擎 run_etf_report,emit task_update/tool_result。"""
        import financial_analyst.buddy.tools as bt
        code = spec.get("code"); name = spec.get("name") or code
        bg_id = f"etfbg_{code}_{turn_id}" if "turn_id" in dir() else f"etfbg_{code}"
        st.merge_meta_sub(sid, "bg", bg_id, {"kind": "etf_report", "code": code, "status": "running"})
        _emit(sid, "task_update", task_id=bg_id, kind="etf_report", code=code, status="running",
              note=f"{name} ETF 研报生成中")
        loop = asyncio.get_running_loop()
        try:
            def _run():
                tool = bt.get_tool("run_etf_report")
                if tool is None:
                    raise RuntimeError("引擎 run_etf_report 不可用")
                return tool.run(code=code, asof=spec.get("asof"))
            res = await loop.run_in_executor(None, _run)
            ok = not getattr(res, "is_error", False)
            content = str(getattr(res, "content", ""))
            _emit(sid, "tool_result", tool="ww_etf_report_run", ok=ok, content=content[:4000],
                  artifact={"kind": "report_md", "page": None, "channel": None,
                            "payload": {"code": code, "name": name}})
            _emit(sid, "task_update", task_id=bg_id, kind="etf_report", code=code,
                  status="done" if ok else "error")
            st.merge_meta_sub(sid, "bg", bg_id, {"status": "done" if ok else "error"})
        except Exception as e:  # noqa: BLE001
            _emit(sid, "tool_result", tool="ww_etf_report_run", ok=False, content=f"ETF 研报失败: {e}")
            _emit(sid, "task_update", task_id=bg_id, kind="etf_report", code=code, status="error")
            st.merge_meta_sub(sid, "bg", bg_id, {"status": "error"})
```

（实现期对齐 `_run_report_bg` 现有的 `bg_id` 生成、`_emit` 签名、`st.merge_meta_sub` 真实方法名与作用域内可见变量;`run_etf_report` 真实入参以 `engine/.../buddy/tools.py:2118` 的 `_tool_etf_report` 签名为准。）

- [ ] **Step 6: 写后台跑道分发测试（`tests/test_console_api.py`）**

```python
def test_bg_kinds_includes_etf_report():
    """守护:_spawn_bg 能分发 etf_report(防回归只认 report)。"""
    import guanlan_v2.console.api as capi
    assert "etf_report" in capi._BG_KINDS and "report" in capi._BG_KINDS
```

- [ ] **Step 7: 运行验证** — `python -m pytest tests/test_console_tools.py tests/test_console_api.py -k "etf or bg_kinds" -v`（PASS）

- [ ] **Step 8: Checkpoint** — `python -m pytest tests/test_console_tools.py tests/test_console_api.py -q`

---

## Task B5: 注册 B 阶段工具 + 扩 CONSOLE_ALLOWED + 提示词 + 计数

**Files:** `guanlan_v2/console/tools.py`、`guanlan_v2/console/api.py`、`tests/test_console_tools.py`

- [ ] **Step 1: `specs` 末尾追加 4 条**

```python
        ("ww_factor_compose",
         "多因子合成(equal/ic/icir 加权)→ 样本外 OOS 报告 + 各腿权重。用户说『把这几个因子合成/做个多因子模型』时用。只评测不入库。",
         {"type": "object", "properties": {
             "members": {"type": "array", "items": {"type": "string"},
                         "description": "≥2 个 zoo 因子表达式或已注册因子名"},
             "method": {"type": "string", "enum": ["equal", "ic", "icir"], "default": "equal"},
             "universe": {"type": "string", "default": "csi300"},
             "oos_frac": {"type": "number", "default": 0.3}},
          "required": ["members"]},
         _wrap(factor_compose_impl), "seconds", False),
        ("ww_feature_build",
         "物化特征工程(真 X/y)→ 逐特征对前向收益的 RankIC + 覆盖统计。搭多特征矩阵/做模型前的特征体检用。",
         {"type": "object", "properties": {
             "features": {"type": "array", "items": {"type": "string"}, "description": "zoo 特征表达式列表"},
             "label": {"type": "string", "description": "标签表达式;留空=前向收益"},
             "fwd_days": {"type": "integer", "default": 5},
             "universe": {"type": "string", "default": "csi_fast"},
             "oos_frac": {"type": "number", "default": 0.0}},
          "required": ["features"]},
         _wrap(feature_build_impl), "seconds", False),
        ("ww_factor_fields",
         "列出 zoo 因子 DSL 的合法字段(价量/基本面/技术/财务/参照)+算子+范例。写因子表达式前查字段名,避免拼错被校验拒绝。",
         {"type": "object", "properties": {}},
         _wrap(factor_fields_impl), "instant", False),
        ("ww_etf_report_run",
         "生成 ETF 深度研报(持仓/技术/申赎/折溢价/风控,后台 5-8 分钟,完成通知)。需用户确认。",
         {"type": "object", "properties": {
             "code": {"type": "string", "description": "ETF 代码,如 SH510300 或 510300"},
             "name": {"type": "string"}, "asof": {"type": "string"}},
          "required": ["code"]},
         _wrap(etf_report_run_impl), "minutes", True),
```

- [ ] **Step 2: 扩 `CONSOLE_ALLOWED`**，在 A 新增的 ww_ 行后加:

```python
    "ww_factor_compose", "ww_feature_build", "ww_factor_fields", "ww_etf_report_run",  # B 新增
```

- [ ] **Step 3: `_SYSTEM_PROMPT` 续写 + 纪律 3 改写**

「另有:…」末尾追加:

```
因子合成 ww_factor_compose、物化特征 ww_feature_build、查 DSL 字段 ww_factor_fields(写因子表达式前先查合法字段名)、ETF 研报 ww_etf_report_run(后台,需确认)。
```

纪律 3 改为(强调先查字段):

```
3. 因子表达式用 zoo DSL(如 rank(-delta(close,20))、-stddev(returns,20)、rank(roe));不确定有哪些合法字段/算子先调 ww_factor_fields 查,别凭空猜字段名。
```

- [ ] **Step 4: 更新守护计数（`test_engine_profile_excludes_ww_but_console_whitelist_resolves`）**

```python
    assert len(out["registered_ww"]) == 24                    # B 后:20 + 4
    assert out["console_n"] == 42 and out["console_missing"] == []
    assert out["explicit_n"] == 42 and out["explicit_ww_n"] == 24
```

- [ ] **Step 5: 全量验证** — `python -m pytest tests/test_console_tools.py tests/test_console_api.py -q`（全绿）

- [ ] **Step 6: 真机端到端验证(Phase B 验收)**

1. 「写因子前先告诉我有哪些字段能用」→ `ww_factor_fields` 真返回词表。
2. 「把 rank(roe) 和 mom_60 用 ic 加权合成,看 OOS」→ `ww_factor_compose` 真出 RankIC + 权重。
3. 「物化 rank(roe), mom_60 两个特征看逐特征 IC」→ `ww_feature_build` 真出覆盖+IC。
4. 「给 510300 出个 ETF 研报」→ `ww_etf_report_run`(确认)→ 后台跑道 task_update→done,可翻阅。
Expected: 闭环「查字段→测因子→合成→入库→选股」端到端可用。

---

# Phase C — 自省 / 自学治本

## Task C1: `ww_capabilities` impl(工具自省,免确认)

**Files:** `guanlan_v2/console/tools.py`、`tests/test_console_tools.py`

- [ ] **Step 1: 写失败测试**

```python
def test_capabilities_impl_lists_reachable_tools(monkeypatch):
    import types
    tools = [types.SimpleNamespace(name="ww_factor_analyze", description="因子分析\n第二行",
                                   confirm_required=False, cost_hint="seconds"),
             types.SimpleNamespace(name="ww_report_run", description="深度研报",
                                   confirm_required=True, cost_hint="minutes"),
             types.SimpleNamespace(name="some_hidden_tool", description="不在白名单",
                                   confirm_required=False, cost_hint="instant")]
    monkeypatch.setattr(ct, "_buddy_tools_mod",
                        lambda: types.SimpleNamespace(TOOL_REGISTRY=tools))
    monkeypatch.setattr(ct, "CONSOLE_ALLOWED", {"ww_factor_analyze", "ww_report_run"})
    res = ct.capabilities_impl()
    assert res["ok"] is True
    assert "ww_factor_analyze" in res["content"] and "ww_report_run" in res["content"]
    assert "some_hidden_tool" not in res["content"]          # 白名单外不列
    assert "需确认" in res["content"]                          # ww_report_run 标确认
    assert "因子分析" in res["content"] and "第二行" not in res["content"]  # 只取首行
```

- [ ] **Step 2: 运行验证失败** — `python -m pytest tests/test_console_tools.py -k capabilities -v`（FAIL）

- [ ] **Step 3: 写实现**

```python
def capabilities_impl() -> Dict[str, Any]:
    """列出帷幄当前真正能调用的全部工具(TOOL_REGISTRY ∩ CONSOLE_ALLOWED)+ 用途/确认/成本。
    自省工具:回答『你能做什么/有哪些工具』。"""
    try:
        bt = _buddy_tools_mod()
        rows = [t for t in bt.TOOL_REGISTRY if t.name in CONSOLE_ALLOWED]
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"工具清单读取失败: {e}", "artifact": None}
    lines = []
    for t in sorted(rows, key=lambda x: x.name):
        head = str(getattr(t, "description", "")).splitlines()[0][:70]
        flag = "(需确认)" if getattr(t, "confirm_required", False) else ""
        lines.append(f"· {t.name}{flag} — {head}")
    return {"ok": True,
            "content": f"我当前能调用 {len(rows)} 个工具:\n" + "\n".join(lines), "artifact": None}
```

- [ ] **Step 4: 运行验证通过** — `python -m pytest tests/test_console_tools.py -k capabilities -v`（PASS）

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_console_tools.py -q`

---

## Task C2: `ww_endpoints` impl(后端能力地图 + 诚实可达性,免确认)

**Files:** `guanlan_v2/console/tools.py`、`tests/test_console_tools.py`

- [ ] **Step 1: 写失败测试**

```python
def test_endpoints_impl_marks_reachability(monkeypatch):
    fake_openapi = {"paths": {
        "/screen/run": {"post": {"summary": "九视角选股"}},
        "/workflow/garch": {"post": {"summary": "GARCH 波动预测"}},
    }}
    monkeypatch.setattr(ct, "_self_get", lambda path, timeout=30: fake_openapi)
    res = ct.endpoints_impl()
    assert res["ok"] is True
    c = res["content"]
    assert "/screen/run" in c and "/workflow/garch" in c
    assert "可直接调" in c and "仅界面可达" in c
```

- [ ] **Step 2: 运行验证失败** — `python -m pytest tests/test_console_tools.py -k endpoints_impl -v`（FAIL）

- [ ] **Step 3: 写实现**

```python
# ww_ 工具能直接触达的后端路径(随新增工具同步;C2 诚实可达性标注依据)
_WW_REACHABLE_ENDPOINTS = {
    "/screen/run", "/screen/factors", "/seats/decide", "/seats/decisions",
    "/cards/list", "/cards", "/factor/report2", "/backtest/vector",
    "/factorlib/save", "/workflow/compose", "/feature/build", "/openapi.json",
}


def endpoints_impl(filter_prefix: str = "") -> Dict[str, Any]:
    """列出后端能力地图(GET /openapi.json),诚实标注每项『我可直接调 / 仅界面可达』。
    用于回答『观澜平台能做什么』+ 诚实降级(有功能但我调不到→请在界面用),不冒充能调。"""
    try:
        r = _self_get("/openapi.json")
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "content": f"能力地图读取失败: {e}", "artifact": None}
    paths = (r or {}).get("paths") or {}
    pref = (filter_prefix or "").strip()
    rows = []
    for path in sorted(paths):
        if pref and not path.startswith(pref):
            continue
        methods = ",".join(sorted(m.upper() for m in paths[path] if m.lower() in
                                  ("get", "post", "put", "delete", "patch")))
        summary = ""
        for m in paths[path].values():
            if isinstance(m, dict) and m.get("summary"):
                summary = str(m["summary"])[:50]; break
        mark = "可直接调" if path in _WW_REACHABLE_ENDPOINTS else "仅界面可达"
        rows.append(f"· {methods} {path} [{mark}] {summary}")
    if not rows:
        return {"ok": True, "content": "(无匹配端点)", "artifact": None}
    head = f"后端能力地图(共 {len(rows)} 个端点;『仅界面可达』= 我没有对应工具、需你在网页操作):\n"
    return {"ok": True, "content": head + "\n".join(rows[:120]), "artifact": None}
```

- [ ] **Step 4: 运行验证通过** — `python -m pytest tests/test_console_tools.py -k endpoints_impl -v`（PASS）

- [ ] **Step 5: Checkpoint** — `python -m pytest tests/test_console_tools.py -q`

---

## Task C3: 注册 C 阶段工具 + 白名单 + 失败→记忆纪律 + 计数

**Files:** `guanlan_v2/console/tools.py`、`guanlan_v2/console/api.py`、`tests/test_console_tools.py`

- [ ] **Step 1: `specs` 末尾追加 2 条**

```python
        ("ww_capabilities",
         "列出我(帷幄)当前能调用的全部工具及用途。用户问『你能做什么/有哪些功能/会用什么工具』,或我不确定该用哪个工具时,先调它自查。",
         {"type": "object", "properties": {}},
         _wrap(capabilities_impl), "instant", False),
        ("ww_endpoints",
         "列出观澜后端的能力地图(所有端点),并标注哪些我能直接调、哪些只能在界面操作。用户问『平台/系统能做什么』或我遇到自己没有的能力时用。",
         {"type": "object", "properties": {
             "filter_prefix": {"type": "string", "description": "可选,只列某前缀,如 /workflow 或 /seats"}}},
         _wrap(endpoints_impl), "instant", False),
```

- [ ] **Step 2: 扩 `CONSOLE_ALLOWED`**，B 行后加:

```python
    "ww_capabilities", "ww_endpoints",  # C 新增
```

- [ ] **Step 3: `_SYSTEM_PROMPT` 续写 + 失败→记忆纪律**

「另有:…」末尾追加:

```
自省 ww_capabilities(列我有哪些工具)、能力地图 ww_endpoints(平台能做什么 + 哪些我调不到)。
```

纪律区追加两条:

```
10. 不确定自己能不能做某事 / 该用哪个工具时,先调 ww_capabilities 看自己有哪些工具;用户问『平台能做什么』时调 ww_endpoints。
11. 遇到平台确实没有的能力,或某工具反复失败,诚实告诉用户『这个我目前做不到/需在界面操作』,并用 ww_memory_write 把这个能力缺口记下来(scope=global),供后续补齐;绝不假装做到。
```

- [ ] **Step 4: 更新守护计数（终值）**

```python
    assert len(out["registered_ww"]) == 26                    # C 后:24 + 2
    assert out["console_n"] == 44 and out["console_missing"] == []
    assert out["explicit_n"] == 44 and out["explicit_ww_n"] == 26
```

- [ ] **Step 5: 全量验证** — `python -m pytest tests/ -q`（全仓全绿）

- [ ] **Step 6: 真机端到端验证(Phase C 验收)**

1. 「你现在能做什么/有哪些工具」→ `ww_capabilities` 列全 26 个工具。
2. 「观澜平台都能干啥」→ `ww_endpoints` 列能力地图,GARCH/归因等标「仅界面可达」。
3. 「帮我做个 LSTM 选股模型」(帷幄无此工具)→ 诚实回「`/model/lstm` 在工作流界面可用,我目前没有直接工具」+ `ww_memory_write` 记缺口。
Expected: 自省与诚实降级生效,不冒充能调。

---

## 收尾(全阶段后)

- [ ] **更新项目记忆**:`memory/` 写 `weiwo-capability-expansion.md`(A/B/C 交付、新工具清单、闭环、守红线点),`MEMORY.md` 加一行精简指针(MEMORY.md 已超限,索引行务必短)。
- [ ] **整合审查**:两段评审(`ecc:python-reviewer` + 跨文件契约整合审查),确认 `CONSOLE_ALLOWED`/specs/`_SYSTEM_PROMPT`/守护计数四处一致。

---

## 自审(写完计划后的 fresh-eyes 检查)

**1. Spec 覆盖**
- spec §5 A1 → Task A1 ✓;§5 A2(11 白名单)→ Task A3 Step 2 ✓;§5 A3 → Task A2 ✓
- spec §6 B1 → B1 ✓;B2 → B2 ✓;B3 → B3 ✓;B4 → B4 ✓
- spec §7 C1 → C1 ✓;C2 → C2 ✓;C3(纪律)→ C3 Step 3 ✓
- spec §3 接线机制 / §8 横切 → 各 phase 的注册+白名单+提示词+计数任务 ✓
- spec §9 测试 → 各 impl 单测 + 守护计数 + 真机 e2e ✓

**2. Placeholder 扫描**:无 TBD/TODO;B4 Step 5c 标注的「对齐 `_run_report_bg` 现有签名」是对既有作用域可见变量的指引(已给完整代码骨架),非占位。

**3. 类型/命名一致性**:`factorlib_save_impl`/`update_data_impl`/`news_collect_impl`/`factor_compose_impl`/`feature_build_impl`/`factor_fields_impl`/`etf_report_run_impl`/`capabilities_impl`/`endpoints_impl` 在 impl 定义处与 specs 的 `_wrap(...)` 引用处一致;`CONSOLE_ALLOWED` 名字与 specs 的 `name` 一致;守护计数 20/38→24/42→26/44 与「每阶段新增数」自洽(17+3=20,白名单 +11 → console_n 24+3+11=38;+4 → 24/42;+2 → 26/44)。
