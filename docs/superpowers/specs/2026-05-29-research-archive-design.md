# 研究档案 (Research Archive) 设计 · SP-E

> 状态: 已批准, 待落 plan
> 日期: 2026-05-29
> 子项目: 量化研究流水线 SP-E ("评测 → 改进 → 再评测" 迭代闭环)

## 目标

把因子/合成的评测运行 (factor_report / factor_compose 结果) **opt-in 持久化**成 append-only 运行日志, 支持列出、看某标的的指标历史 (版本趋势)、对比两次运行 (指标 diff)。结论沉淀 = 运行的 note/tags + 对比叙述。形成"评测→改进→再评测"的迭代闭环。自包含, 纯 stdlib。

## 背景与定位

流水线: A 评测✅ / B 炼因子✅ / B.1b 基本面✅ / D 多因子合成✅ / **E 研究档案 (本文)** / C 工作台UI / B.2 事件。

### 现状基线
- **结果类型已存在**: `factor_report` → `FactorReport` (`factors/eval/report.py`: meta/ic/quantile/portfolio/characteristics); `factor_compose` → `ComposeResult` (`factors/compose/compose.py`: method/members/weights/composite(FactorReport)/member_oos/verdict)。两者目前**都不持久化**。
- **持久化范式**: `UserFactorStore` (`factors/forge/store.py`, json at `~/.financial-analyst/factors/`, honor `$FINANCIAL_ANALYST_HOME`, 可注入 root); wisdom store (md cards); bench/snapshot cache (`~/.financial-analyst/cache`)。E 复用"可写根 + 注入 root"范式, **不复用 wisdom md** (那是人写经验, 不重复)。
- **工具**: `_tool_factor_report` / `_tool_factor_compose` 在 buddy/tools.py, 内部已持有 FactorReport / ComposeResult 对象。

### 已锁定决策 (brainstorm)
- 形态 = **评测运行日志** (opt-in), 不做独立结论卡 (wisdom 已管)。
- 捕获 = report/compose 工具加 `archive=true` flag (opt-in, 不污染探索性运行), 不自动归档每次。

## 范围

### 做
1. 新建 `factors/research/` 模块: `archive.py` (RunRecord + ResearchArchive + 两个 builder) + `__init__.py`。
2. 集成: `_tool_factor_report` / `_tool_factor_compose` 加 `archive: bool=False` + `note: str=""` 参数, 成功后追加运行记录。
3. 新工具 `research_log` (列 / 历史 / 对比)。
4. 确定性单测。

### 不做
- 独立结论卡 md 系统 → wisdom store 已有。
- 趋势图表可视化 → C 工作台UI。
- 自动归档每次运行 → 用 opt-in。
- 新依赖 → 纯 stdlib (json/datetime/dataclasses)。

## 架构

`factors/research/archive.py`:

### RunRecord (dataclass, JSON-safe)
```python
@dataclass
class RunRecord:
    id: str             # r0001 计数器 (仿 wisdom next_id)
    timestamp: str      # datetime.now().isoformat() (信息性, 测试不断言精确值)
    kind: str           # "report" | "compose"
    target: str         # report: 因子名/表达式; compose: f"{method}:[m1,m2,...]"
    formula: str        # 解析出的表达式/公式 (re-forge 后变了 → 版本 diff 可见; 注册 alpha 为其 formula_text)
    universe: str
    freq: str
    start: str
    end: str
    metrics: dict       # 扁平数值: ic_mean/icir/rank_ic/rank_icir/sharpe/ann_return/max_drawdown/turnover/win_rate/coverage; compose 另含 verdict(str)/members(list)/weights(dict)
    note: str = ""
    tags: list = field(default_factory=list)
```

### ResearchArchive
- 路径: `_default_research_root()` = `$FINANCIAL_ANALYST_HOME/research` else `~/.financial-analyst/research`; 文件 `runs.jsonl`。`__init__(root=None)` 可注入 (测试用 tmp_path)。
- `load() -> list[RunRecord]`: 读 JSONL, **逐行容错** (坏行 logger.warning 跳过, 不崩); 缺文件 → []。
- `append(record_without_id) -> RunRecord`: 分配 id (`r{len(existing)+1:04d}`) + timestamp (若未填), 追加一行 JSONL (mkdir root)。
- `list(kind=None, target=None) -> list[RunRecord]`: 过滤 (kind / target 子串)。
- `history(target) -> list[RunRecord]`: 同 target 的运行, 按 timestamp/id 升序 (看指标版本趋势)。
- `compare(id_a, id_b) -> dict`: 取两条记录, 对公共数值 metrics 求 diff (b - a 或 a - b, 固定一个方向并标注), 返回 {a, b, metric_diffs, targets, configs}。缺 id → 在 dict 里标错, 不抛。

### builders (archive.py)
- `record_from_report(report: FactorReport, *, note="", tags=()) -> RunRecord` (无 id/timestamp, 由 append 填): kind="report"; target=report.meta.factor; formula=report.meta.factor (报告无 formula 字段则用 factor 名/表达式本身); universe/freq/start/end 从 meta; metrics 从 ic+portfolio+characteristics 抽扁平。
- `record_from_compose(res: ComposeResult, *, note="", tags=()) -> RunRecord`: kind="compose"; target=f"{res.method}:[{','.join(res.members)}]"; formula 同 target; 从 res.composite (FactorReport) 抽 metrics + 加 verdict/members/weights。res.composite 为 None (失败) 时 metrics 尽量空。

## 工具集成 (buddy/tools.py)
- `_tool_factor_report(..., archive: bool=False, note: str="")`: status ok 后, 若 archive → `ResearchArchive().append(record_from_report(rpt, note=note))`, 在输出尾加 "✓ 已归档 (id=rNNNN)"。
- `_tool_factor_compose(..., archive: bool=False, note: str="")`: 同理用 record_from_compose。
- 新 `_tool_research_log(target: str="", compare: str="")`:
  - `compare="id_a,id_b"` → 渲染两次运行的指标 diff 表。
  - `target=X` (非空) → 渲染 X 的运行历史 (时间序 + 各次关键指标, 看趋势)。
  - 都空 → 列最近 N (默认 20) 条运行 (id/kind/target/freq/关键指标/note)。
  - 注册 Tool: cost_hint="fast"。
- input_schema 给 report/compose 工具加 archive(boolean)/note(string); research_log 加 target/compare。

## 错误处理
- runs.jsonl 缺失 → 空; 坏行 → 跳过 + warning, 不崩。
- compare 缺 id → 结构化标错 (不抛)。
- archive 写失败 (磁盘/权限) → 工具不因归档失败而整体失败: try/except, 输出 "⚠ 归档失败: ..." 但报告主体照常返回。
- record_from_compose 在 res.composite=None 时不崩 (metrics 空)。

## 测试策略 (确定性单测, 注入 tmp root)
1. **append→load 往返**: append 两条 → load 得 2 条, id=r0001/r0002 递增, 字段保真。
2. **record_from_report**: 用合成 FactorReport (或最小构造) → RunRecord kind/target/metrics 正确。
3. **record_from_compose**: 合成 ComposeResult → kind=compose, target 含 method+members, metrics 含 verdict。
4. **history**: 同 target 多条 + 不同 target → history(target) 只返回该 target, 时间序。
5. **compare**: 两条 → metric_diffs 数值正确; 缺 id → 结构化标错不抛。
6. **坏 JSONL 行容错**: 手写一个含坏行的 runs.jsonl → load 跳过坏行返回好行, 不崩。
7. **工具 archive 往返**: mock report/compose 运行 (stub loader) + archive=true → runs.jsonl 有该条; research_log() 列出; research_log(compare=...) 出 diff。
8. **archive 失败不拖垮报告**: 注入写失败 → 工具仍返回报告主体 + 归档失败提示。
9. **回归 + 不污染注册表**: factor_report/factor_compose 现有测试不回归; 不用 _clear_registry_for_tests。

## 验收标准 (Definition of Done)
- `ResearchArchive` append/load/list/history/compare 工作, JSONL 容错。
- record_from_report / record_from_compose 正确抽指标。
- factor_report/factor_compose `archive=true` 写运行日志; `research_log` 列/历史/对比可用。
- 归档失败不拖垮报告主体。
- 纯 stdlib 无新依赖; 上述 9 组单测全绿; 现有 eval/compose/forge/zoo/buddy 不回归; 不污染注册表。
