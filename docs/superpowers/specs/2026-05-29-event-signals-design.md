# 事件信号 (Event Signals) 设计 · SP-B.2

> 状态: 待用户 review
> 日期: 2026-05-29
> 子项目: 量化研究流水线 SP-B 的第 2 块 — 事件触发因子的 DSL 支持 + 事件研究回测

## 目标

让**事件触发型因子** (crossover / 突破 / 连续放量 等离散触发) 既能在白名单 DSL 里**表达**, 又能被**正确评测** —— 截面 IC/十分位对稀疏布尔触发是错口径, 需要**事件研究 (event study)**: 把每次触发当一个事件, 统计事件后多个 horizon 的前向收益。引擎自包含, 可单测, 经 agent 工具 + 直连 REST 暴露。

## 背景与定位

SP-B 拆分: B (炼因子 v1, 只价量) ✅ → B.1b (7 daily_basic 进 DSL) ✅ → **B.2 事件信号 (本文)**。

### 现状基线 (已勘察)

- **DSL** (`factors/zoo/expr.py`): `FACTOR_VOCAB` + `compile_factor` 受限 eval (`{"__builtins__": {}}` + 字段/算子白名单 ns)。算子在 `factors/zoo/operators.py`。`filter_where(x, mask)` = `x.where(mask)` **已存在** (WHERE)。**无 `cross`** (crossover)。比较 (`>` `<`) 返回布尔 Series, `&`/`|`/`*` 在 eval 表达式里直接可用 (语法, 不在 ns)。
- **forge** (`factors/forge/forge.py`): `_SYSTEM` 现**显式拒绝**"连续/金叉/突破"事件 (设 `out_of_vocab=true`, error 指向 "事件→SP-B.2")。
- **截面评测** (`factors/eval/report.py`): `forward_simple_returns(panel, n)` = `close(t+n)/close(t)-1` 逐 code (**可直接复用**); `ReportMeta` 结构; `build_report`(纯)/`factor_report`(I/O) 的纯/IO 边界 + 4 错误态不抛异常范式; `resolve_universe_codes` + `PanelData.from_loader` 加载路径。
- **REST** (`buddy/server.py`): C.1 的 6 个 `/factor/*` 同步直连端点 + `_jsonable` (NaN/Inf/numpy→null) + Pydantic Req 模型范式。panel 索引 `(datetime, code)` MultiIndex。
- **数据**: panel 有 close/open/high/low/volume/vwap/amount/returns/industry + 7 daily_basic。**没有**公司事件日期 (财报/分红披露日)。

### 已锁定决策 (本次 brainstorm)

- **接入深度**: 引擎 + agent 工具 + REST 端点 (不加新 UI 模式; 为未来 workbench 第 5 模式留 REST 接口)。
- **事件范围 v1**: 价量/基本面**派生的技术事件** (crossover / 突破 / 连续 / 放量)。公司事件日期出 v1 (panel 无此数据, 记为未来数据依赖)。
- **事件研究**: 每次触发 = 一个事件, 统计事件后 horizon ∈ {1,5,10,20} 日前向收益, **市场调整** (减同日等权全市场前向收益 = abnormal)。
- **触发表达**: 触发因子 = 求值为**布尔/稀疏**信号的 DSL 表达式 (cross / 比较 / ts_*), 经 `event_report` 跑事件研究 (而非 forge 入库 + 截面)。forge 暂仍截面; out_of_vocab 提示改指向 `event_report` 工具。

## 范围

### 做
1. **DSL**: `operators.py` 加 `cross(a, b)` + `expr.py` `FACTOR_VOCAB`/ns 注册。
2. **引擎**: 新 `factors/eval/event.py` — `EventReport`/`EventHorizon` + `build_event_report`(纯) + `event_report`(I/O)。
3. **工具**: `buddy/tools.py` 加 `event_report` agent 工具; forge `out_of_vocab` 提示更新。
4. **REST**: `buddy/server.py` 加 `POST /factor/event` + `EventReq`。
5. **测试**: cross 单测 + 合成面板事件研究单测 + 高事件率警告 + no_events 态 + REST TestClient 测试。

### 不做
- 公司事件日期 (财报/分红披露) — panel 无数据。
- forge 自动产出 tagged 事件因子 + 事件型 quick-eval (未来增量)。
- workbench 事件信号 UI 模式 (未来 C.5, REST 已留口)。
- 多事件去重/min-gap (v1 保留全部触发; overlapping 的 t 值膨胀作为已知简化标注)。

## DSL: `cross` 算子

`operators.py`:
```python
def cross(a, b):
    """a 上穿 b: a[t-1] <= b[t-1] 且 a[t] > b[t] → 1.0, 否则 0.0 (逐 code)。
    死叉 = cross(b, a)。a/b 为 Series (或标量); delay 已逐 code。"""
    prev_a = delay(a, 1) if hasattr(a, "index") else a
    prev_b = delay(b, 1) if hasattr(b, "index") else b
    up = (a > b) & (prev_a <= prev_b)
    return up.astype(float)
```
`expr.py`: `FACTOR_VOCAB` 算子段加 `cross(x,y)`; `compile_factor` ns 加 `"cross": _ops.cross`。

**连续/放量无需新算子** (示例写进 vocab 注释 + forge few-shot): `ts_min((close > delay(close,1)) * 1.0, 3)` (连续 3 天涨) · `(volume > ts_mean(volume, 5) * 1.5)` (放量) · `&`/`*` 组合 AND。

## 引擎: `factors/eval/event.py`

```python
@dataclass
class EventHorizon:
    h: int                 # 前向天数
    n: int                 # 该 h 有有效前向收益的事件数
    mean_ret: float        # 原始平均前向收益
    mean_excess: float     # 市场调整 (减同日等权全市场前向收益)
    win_rate: float        # 原始收益 > 0 的比例
    t_stat: float          # excess 的 t 值 = mean_excess / (std_excess / sqrt(n))

@dataclass
class EventReport:
    factor: str
    universe: str
    start: str
    end: str
    n_dates: int
    n_codes: int
    n_events: int
    event_rate: float                       # n_events / 有效 (date,code) obs
    horizons: List[EventHorizon]            # {1,5,10,20}
    car_curve: List[Tuple[int, float]]      # 事件后 1..max_h 日平均超额 (CAR-like)
    by_year: List[Tuple[str, int, float]]   # (年, 事件数, 主 horizon=5 平均超额)
    warnings: List[str]
    status: str = "ok"                      # ok/empty_universe/load_error/compute_error/no_events
    error: str = ""
```

**事件研究算法** (`build_event_report(panel, compute, config, horizons=(1,5,10,20))`, 纯):
1. `sig = compute(panel)` (compute_error → 结构化返回)。触发掩码 `fired = sig.astype(float) > 0` (布尔/1.0 都 truthy); `event_idx = fired[fired].index`。空 → `no_events`。
2. `event_rate = n_events / sig.dropna().shape[0]`; `> 0.5` → warnings 加 "更像连续因子, 用 factor_report"。
3. 每 `h`: `fwd = forward_simple_returns(panel, h)`; `mkt = fwd.groupby(level="datetime").transform("mean")`; `excess = fwd - mkt`; 取 `event_idx` 上 dropna → `n / mean_ret / mean_excess / win_rate / t_stat` (`navg>0` 守卫, n<2 → t=nan)。
4. `car_curve`: `d in 1..max(horizons)` 的 `excess` 在 event_idx 的均值 (逐日)。
5. `by_year`: 事件按 `datetime.year` 分组 → (年, 数, h=5 excess 均值)。
6. **市值/复杂度守卫**: nav/std 退化 (std=0 或 n<2) → t_stat=nan, 不崩。

**I/O** `event_report(spec_or_expr, config, horizons)`: `resolve_universe_codes` → 空 `empty_universe`; `PanelData.from_loader` 异常 → `load_error`; 名字先 `registry.get` 再 fallback `compile_factor(expr)` (同 factor_report); 调 `build_event_report`。**永不抛**, 4+1 错误态全返回 EventReport。复用 `EvalConfig` (universe/start/end; freq 不适用事件研究)。

## 工具: `event_report` (buddy/tools.py)

`TOOL_REGISTRY` 加 `event_report`, cost_hint=minutes, confirm_required=True: 触发表达式 (agent 现搭, 同 factor_test 白名单) 或注册名 → 中文事件研究报告 (事件数/事件率 + horizon 表 raw/excess/win/t + CAR + 逐年 + 高事件率/no_events 提示)。经模块属性访问调 `_event_mod.event_report` (便于 monkeypatch)。forge `_SYSTEM` 的 out_of_vocab 措辞: 事件想法 → "用 event_report 工具跑事件研究 (写 cross/比较/ts_* 触发表达式)", 不再说 "SP-B.2 待做"。

## REST: `POST /factor/event`

```python
class EventReq(BaseModel):
    expr_or_name: str
    universe: str = "csi300_active"
    start: Optional[str] = None
    end: Optional[str] = None
    horizons: list = [1, 5, 10, 20]
    archive: bool = False   # 事件研究默认不归档 (档案 schema 是 report/compose; 事件 metrics 不同)
    note: str = ""
```
`build_app()` 加 `@app.post("/factor/event")` (用 `async def`, 同 report/compose — event_report 无 LLM/`asyncio.run`, 不必像 forge 那样脱离事件循环): 调 `_event_mod.event_report` → `_jsonable(asdict(rpt))`。业务失败 200+status; 内部异常 500+{error} 不泄栈。`archive=true` 暂忽略 (v1 不归档事件; 留字段向后兼容)。交互默认小池 csi300_active。

## 测试 (tests/test_event_signals.py)

1. **cross 单测**: 构造 a/b Series (单 code, a 在某日上穿 b) → `cross(a,b)` 该日=1.0, 其余=0.0; 死叉 `cross(b,a)` 对称。
2. **event_study 合成面板**: 造一个触发只在已知日期/code firing + 已知前向收益 → 断言 `n_events`, `mean_ret`/`mean_excess` 数值, `win_rate`, horizon 表长度=4, car_curve 长度=max_h。
3. **市场调整**: 全市场同日同涨 → excess≈0 (验证减市场)。
4. **高事件率警告**: 喂连续因子 (如 `close`, 恒正) → event_rate 高 → warnings 含提示 (status 仍 ok)。
5. **no_events**: 永不触发的表达式 (如 `cross(close, close*2)`) → status="no_events", 不崩。
6. **empty_universe / compute_error**: stub resolve→[] → empty_universe; 烂表达式 → compute_error。
7. **REST**: `TestClient(build_app())` + stub loader/universe → `POST /factor/event` 200, body 有 horizons/n_events/car_curve, **无 NaN 字面量** (退化输入某指标 null)。
8. 不污染注册表 (不用 `_clear_registry_for_tests`); 控制端 miniconda (pandas 2.3.3) 复跑; 不用 pandas≥2.2-only API。

## 验收标准 (DoD)

- `cross` 算子可用 + 进 vocab/ns; 连续/放量经现有算子可表达 (文档示例)。
- `event_report` 跑任意触发表达式/名 → EventReport (raw+excess horizon 表 + CAR + 逐年 + 事件率), 4+1 错误态不抛异常。
- 市场调整 (excess) 正确; 高事件率 + no_events 有提示/状态。
- agent 工具 `event_report` + `POST /factor/event` 接通; forge out_of_vocab 提示改指向工具。
- 单测全绿 (cross + event_study + REST); 现有 factor_rest / eval / forge 套件不回归; 不污染注册表; 无新依赖 (纯复用 forward_simple_returns + numpy/pandas)。
