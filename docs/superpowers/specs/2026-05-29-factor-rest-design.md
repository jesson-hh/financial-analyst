# 因子直连 REST 端点 (Factor REST) 设计 · SP-C.1

> 状态: 已批准, 待落 plan
> 日期: 2026-05-29
> 子项目: 量化研究流水线 SP-C (工作台UI) 的第 1 块 — 后端直连端点

## 目标

给 `buddy/server.py` 加一组**直连 REST 端点**, 直接调用已建的因子函数 (不走 agent /run 循环), 返回 JSON。让未来的量化工作台页面 (C.2+) 能点一下就拿到因子报告/合成/档案数据, 而不必经 agent。纯后端, FastAPI TestClient 可单测。

## 背景与定位

C (工作台UI) 拆分: **C.1 直连 REST (本文)** → C.2 因子详情页 → C.3 炼因子卡 → C.4 合成+档案 panel。

### 现状基线 (已勘察)
- `buddy/server.py build_app()` (`:150`) 内以 `@app.post`/`@app.get` 闭包定义路由 (app=FastAPI `:182`)。现有路由全是会话/数据/agent 相关; **没有任何直连跑因子工具的端点** (factor_report/forge/compose/research_log/bench 只在 agent /run 内经 tools.py 调)。
- 可复用函数 (已合 main): `factors.eval.factor_report(spec_or_expr, EvalConfig) -> FactorReport`; `factors.forge.forge_factor(idea) -> ForgeResult` + tools 的 `_quick_ic`; `factors.compose.compose_factors(members, EvalConfig, method, train_frac) -> ComposeResult`; `factors.research.ResearchArchive` (list/history/compare); `factors.zoo.bench_runner.run_bench(panel, family, fwd_days) -> DataFrame`; `factors.zoo.registry.list_alphas` + `factors.forge.UserFactorStore.list`。
- dataclass 都 JSON-safe (SP-A 起设计: float/str/list)。但 **NaN/Inf**: `json.dumps` 默认 `allow_nan=True` 输出 `NaN` (非法 JSON, 前端 `JSON.parse` 拒) → REST 层必须把 NaN/Inf → null。
- EvalConfig 默认 universe="csi500" freq="month" 近 2 年 → 分钟级。**交互端点默认改小池 (csi300_active ~120) + 短窗** 求秒级 (用户已定: 同步+快默认)。

### 已锁定决策
- **同步 REST + 快默认** (非异步作业, 非 SSE): 交互路径默认小池/短窗秒级返回, 前端转圈; 大池请求慢但可接受。
- 校正 mock: 端点用后端真口径 (csi 池名, 真指标), 不迁就 mock 的 hs300/拥挤度。

## 范围

### 做
1. 在 `build_app()` 加 6 个直连端点 (见下) + 一个 `_jsonable` NaN→null 序列化助手。
2. FastAPI TestClient 单测 (stub loader, 不依赖真数据)。

### 不做
- UI 页面 (C.2+)。
- 异步作业队列 / SSE 流式 (用同步+快默认)。
- 无数据源的拥挤度/风格暴露/相关性端点 (mock 有但后端无 → 不建)。
- 鉴权 (本地单用户工具, 沿用现有 server 无鉴权)。

## 端点 (均在 build_app 内)

| 方法 | 路径 | 请求 | 响应 (JSON) |
|------|------|------|-------------|
| POST | `/factor/report` | `{expr_or_name, universe?, freq?, start?, end?}` | FactorReport asdict (NaN→null) + `status` |
| POST | `/factor/forge` | `{idea, universe?, quick_eval?}` | ForgeResult asdict + 可选 quick IC dict |
| POST | `/factor/compose` | `{members[], method?, universe?, freq?, train_frac?}` | ComposeResult asdict (含 composite FactorReport) |
| GET | `/factor/archive` | `?target=&compare=` | runs list / history / compare dict |
| GET | `/factor/bench` | `?universe=&family=&since=&until=&max_codes=` | `{rows: [{name, family, rank_ic, rank_ir, ic, hit_rate, state}]}` (批量 IC, 喂因子库横条) |
| GET | `/factor/list` | `?family=` | `{registered: [{name, family, formula}], user: [{name, expr, kpis}]}` |

### 请求模型
用 Pydantic `BaseModel` (server 已用 FastAPI, 仿现有 RunReq `:39`) 定义 `ReportReq`/`ForgeReq`/`ComposeReq`, 字段带默认 (universe 默认 `"csi300_active"` 交互快档, freq 默认 `"month"`, train_frac 0.6)。

### 默认 (交互快档)
- report/compose 默认 `universe="csi300_active"` (~120 只, 秒级), 而非引擎库默认的 csi500。调用方可显式传大池。
- bench 默认 `max_codes=120`。

## 序列化 (关键)
`_jsonable(obj)` 助手: `dataclasses.asdict` 后递归把 `float('nan')`/`inf`/`-inf` → `None` (用 `math.isnan`/`math.isinf`); tuple→list (json 本就转, 但 asdict 保留 tuple → 显式转保险); 其余原样。所有端点响应过这个助手, 保证合法 JSON。FastAPI 默认 JSONResponse 用 `json.dumps`——但其默认 allow_nan 会出 `NaN`; 故**必须**先 sanitize 再返回 (返回 dict, FastAPI 序列化; 或自建 JSONResponse(content=sanitized))。

## 错误处理
- 业务结构化失败 (factor_report/compose 的 status=empty_universe/load_error/compute_error/fit_error; forge out_of_vocab/compile_ok=False) → **HTTP 200**, body 带该 status/error (前端按 status 渲染, 与 agent 工具一致)。
- 请求格式错 (缺 expr_or_name / members<2 / 非法 method) → HTTP 422 (Pydantic) 或 400 + 明确 message。
- 端点内部异常 (未预期) → try/except → HTTP 500 + `{error}` (不泄栈)。
- bench/archive 缺数据 → 200 + 空 rows/list。

## 测试 (tests/test_factor_rest.py, FastAPI TestClient)
用 `TestClient(build_app())` + monkeypatch `resolve_universe_codes` + `get_default_loader` (stub, 仿 test_factor_report_tool / test_compose 的 stub) + 临时 `$FINANCIAL_ANALYST_HOME` (archive)。
1. `POST /factor/report` (stub) → 200, body 有 meta/ic/quantile/portfolio, status="ok", **无 NaN 字面量** (断言 response.json() 不抛 + 检查某 NaN 字段为 null 或数值)。
2. `POST /factor/forge` (mock forge_factor via monkeypatch) → 200, 有 expr/compile_ok。
3. `POST /factor/compose` (2 成员, method=equal, stub) → 200, 有 method/members/verdict/composite。
4. `POST /factor/compose` members<2 → 4xx 或 200+too_few_factors (二选一, 测对齐实现)。
5. `GET /factor/archive` (注入 tmp 档案, 先写两条) → list; `?compare=r0001,r0002` → diff; `?target=X` → history。
6. `GET /factor/bench` (stub panel) → rows 有 name/rank_ic/state。
7. `GET /factor/list` → registered 非空 (内置 alpha), user 列出已入库。
8. **NaN 序列化**: 构造一个会产出 NaN 指标的请求 (或直接单测 `_jsonable({'x': float('nan')})['x'] is None`) → 确认 NaN→null, response.json() 合法。
9. 内部异常 → 500 + error, 不泄栈; 不污染注册表 (不用 _clear_registry_for_tests)。

## 验收标准 (DoD)
- 6 端点在 build_app 注册, 直接调因子函数返回合法 JSON (NaN→null)。
- 业务失败 → 200+status; 格式错 → 4xx; 内部异常 → 500。
- 交互默认小池秒级 (csi300_active)。
- 8+ 组 TestClient 单测全绿; 现有 server/buddy 测试不回归; 不污染注册表; 纯复用 (无新依赖, 无新因子逻辑)。
