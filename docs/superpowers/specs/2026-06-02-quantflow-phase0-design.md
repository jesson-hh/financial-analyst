# QuantFlow Phase 0 实现 Spec · Workflow 契约和骨架

> 状态: 待批准
> 日期: 2026-06-02
> 上游计划: `G:/stocks/strategy/research/2026-06-02-guanlan-agent-quantflow-framework-plan.md` §3.1 / §6.1-6.5 / §12 Phase 0
> 子项目: 观澜 Agent QuantFlow Phase 0 (workflow schema + node registry + runner + artifact store + run log + 3 mock 节点)

## 0. 关键架构决策 (已定, 写在最前)

**架构形态 = hybrid**: 实现层用 Python 函数 + `@node` 装饰器 (函数是真源, 不写 YAML); 存储层用 JSON / YAML workflow 文件 (节点通过字符串 `type` 引用)。两层通过全局 `NodeRegistry` 解耦。

| 决策 | 取舍 | 理由 |
|---|---|---|
| 实现层 = Python `@node` 装饰器 | 不强制 YAML 节点定义 | 量化节点本质是函数, Python 写最自然, IDE/类型/测试齐全 |
| 存储层 = JSON / YAML workflow | 不依赖 Pickle / 包内对象 | workflow 文件要可读、可 diff、可跨版本恢复 |
| 节点身份 = 显式字符串 `type` | **装饰器禁止自动取 `func.__name__`** | refactor 改 Python 路径/函数名不破坏已保存 workflow |
| 注册表查找 = 字符串 `type` -> compute | 不允许通过 import path 找 | 切实做到 Python 路径与 workflow 解耦 |
| Schema = Pydantic v2 | 不自写校验 | 已是 core 依赖, 与既有 `factors/eval` 一致 |
| Artifact 序列化 = JSON / Parquet 双格式 | 按 payload 类型自动选 | DataFrame 走 parquet, dict/list/标量走 JSON |
| Run log = JSONL append | 不引 sqlite | Phase 0 跑通即可, append + tail 读够用 |

这条线锁死后, Phase 1 接真实节点 (`data.pit_snapshot` / `factor.lgb_rank` / `agent.premarket_decision`) 只需写新函数 + `@node`, 不动框架。

## 1. 范围

### 做 (in-scope)

1. 新建 `src/financial_analyst/workflow/` 包 (schema / registry / runner / artifacts / run_log / nodes_mock)。
2. Pydantic schema: `Workflow / Node / Edge / NodeRun / RunResult`。
3. `@node` 装饰器 + 全局 `NodeRegistry` (字符串 type 注册)。
4. `WorkflowRunner` (load -> validate -> topo sort + 显式查环 -> 顺序执行 -> hash 输入 -> 写 artifact + run log -> 失败节点保留现场不抛全局)。
5. `ArtifactStore` (按 payload 类型自动选 JSON / parquet, 目录约定 `workflow_runs/{run_id}/nodes/{node_id}/output.{ext}`)。
6. `RunLog` (JSONL append + read_all + latest_status)。
7. 3 个纯 mock 节点 (`data.constant_universe` / `factor.zeros` / `eval.row_count`)。
8. 6 个 pytest 文件覆盖 schema / registry / runner / artifact / run log + 端到端。

### 不做 (out-of-scope, 推到 Phase 1+)

- **不接** `G:/stocks` 任何代码 (硬规则, §5.1)。
- **不做** PIT 数据节点 / LGB / FM / Agent / RiskOfficer / broker_sim / metrics (Phase 1)。
- **不做** Gate Checker, AI 代搭, UI, REST 端点, 模板库, 证据页, 复盘写回 (Phase 1~4)。
- **不做** 节点并行 / 远程执行 / 异步 (单线程顺序跑就行)。
- **不做** 断点续跑 / 重跑失败节点 (run log 留好, Phase 1 再做)。
- **不引入新依赖** — Pydantic v2 / pandas 2.x / pyarrow 已是 core。

## 2. 模块布局

```
src/financial_analyst/workflow/
  __init__.py                # 导出公开 API
  schema.py                  # Pydantic: Workflow / Node / Edge / NodeRun / RunResult / NodeStatus
  registry.py                # NodeRegistry + @node 装饰器 + RegisteredNode dataclass
  runner.py                  # WorkflowRunner: load -> validate -> topo -> execute
  artifacts.py               # ArtifactStore: write/read, 自动选 json/parquet
  run_log.py                 # RunLog: jsonl append/read/latest_status
  hashing.py                 # input hash 工具 (sort_keys=True 确定性)
  topo.py                    # 拓扑排序 + 显式查环
  nodes_mock.py              # 3 个 mock 节点, 仅供 Phase 0 测试
  errors.py                  # WorkflowError / NodeNotFoundError / CycleError / NodeExecutionError

tests/
  test_workflow_schema.py        # Pydantic 形状 + 必填校验
  test_node_registry.py          # @node 注册/查询/重复 type 报错
  test_workflow_runner.py        # topo sort / 顺序执行 / 失败隔离
  test_artifact_store.py         # json/parquet 路由 + 读写往返
  test_run_log.py                # append/read_all/latest_status
  test_workflow_e2e.py           # 3 节点 workflow JSON -> runner.run -> 断言全部产物
```

## 3. Schema (`schema.py`)

全部 Pydantic v2 `BaseModel`, 字段定义如下。**JSON 形状是契约**, Python 类名变了不影响存储格式。

### 3.1 `NodeStatus` (Enum)

```python
class NodeStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"
```

### 3.2 `Node`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `str` | 是 | workflow 内唯一 ID, 边的端点用这个 |
| `type` | `str` | 是 | 注册表查找键, 形如 `"data.constant_universe"` |
| `params` | `dict[str, Any]` | 否 (默认 `{}`) | 节点参数, runtime 转给 `params_model` 校验 |
| `inputs` | `dict[str, str]` | 否 (默认 `{}`) | `{input_name: "<upstream_node_id>.<output_name>"}` 引用上游产出 |

### 3.3 `Edge`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `from_` | `str` | 是 | 形如 `"<node_id>.<output_name>"` (JSON 字段名 `from`, alias) |
| `to` | `str` | 是 | 形如 `"<node_id>.<input_name>"` |

**说明**: Phase 0 同时支持两种连法 — `inputs` 直接引用 (简洁) 或显式 `edges` (画布友好)。Runner 校验时合并两边构造 DAG。

### 3.4 `Workflow`

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `id` | `str` | 是 | workflow 标识 |
| `name` | `str` | 是 | 人类可读名称 |
| `version` | `int` | 否 (默认 `1`) | 模板版本 |
| `nodes` | `list[Node]` | 是 | 节点列表, `len >= 1` |
| `edges` | `list[Edge]` | 否 (默认 `[]`) | 显式边, 与 node.inputs 并存 |
| `meta` | `dict[str, Any]` | 否 (默认 `{}`) | 自由字段 (owner / mode / created_at, Phase 0 不强约) |

### 3.5 `NodeRun`

一次节点执行的不可变记录, append 进 `run_log.jsonl`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | `str` | 所属 workflow run |
| `workflow_id` | `str` | 冗余, 便于跨 run 查询 |
| `node_id` | `str` | |
| `node_type` | `str` | |
| `status` | `NodeStatus` | |
| `input_hash` | `str \| None` | sha256 前 16 位, hex |
| `output_artifact_uri` | `str \| None` | 形如 `workflow_runs/<run_id>/nodes/<node_id>/output.json` |
| `started_at` | `str` | ISO 8601 UTC |
| `ended_at` | `str \| None` | |
| `duration_ms` | `int \| None` | |
| `error` | `str \| None` | 失败时的异常 message + 类名 (不含 traceback, 保留现场放 artifact 目录里) |

### 3.6 `RunResult`

`WorkflowRunner.run()` 返回值。

| 字段 | 类型 | 说明 |
|---|---|---|
| `run_id` | `str` | UUID4 hex |
| `workflow_id` | `str` | |
| `status` | `NodeStatus` | 整体状态 (任意节点 FAILED -> FAILED; 否则 SUCCESS) |
| `node_runs` | `list[NodeRun]` | 按执行顺序 |
| `artifacts_root` | `str` | `workflow_runs/<run_id>/` 绝对路径 |

## 4. NodeRegistry (`registry.py`)

### 4.1 `RegisteredNode` (dataclass / Pydantic)

```python
@dataclass(frozen=True)
class RegisteredNode:
    type: str                              # 字符串身份, e.g. "data.constant_universe"
    compute: Callable[..., Any]            # 装饰的原函数
    params_model: type[BaseModel] | None   # 参数校验, None = 不校验
    outputs_model: type[BaseModel] | None  # 输出形状校验, None = 不校验
    risk: str                              # "normal" | "intraday" | "advice" | "live"
    pit: bool                              # 是否要求 PIT 输入 (Phase 0 仅记录, runner 不强制)
    meta: dict[str, Any]                   # 任意元数据
```

### 4.2 `@node` 装饰器签名

```python
def node(
    type: str,                                       # 必填, 字符串身份
    params_model: type[BaseModel] | None = None,
    outputs_model: type[BaseModel] | None = None,
    risk: str = "normal",
    pit: bool = False,
    **meta: Any,
) -> Callable[[Callable], Callable]:
    """注册一个工作流节点。

    type 必须显式传入, 不允许默认取 func.__name__ ——
    这是 Python 路径与 workflow 文件解耦的硬约束。

    被装饰的函数签名: compute(params, inputs) -> outputs
      - params: dict (若 params_model != None, 会先用它 model_validate)
      - inputs: dict[str, Any] (上游节点产出, key 与 Node.inputs key 对齐)
      - outputs: dict (若 outputs_model != None, 会用它 model_validate 校验形状)
    """
```

**强约束**: 装饰器内部 `assert type, "type 必填, 不允许取 func.__name__"`。重复注册同 type 抛 `ValueError`。

### 4.3 `NodeRegistry` 单例

```python
class NodeRegistry:
    _registry: dict[str, RegisteredNode] = {}

    @classmethod
    def register(cls, node: RegisteredNode) -> None: ...

    @classmethod
    def get(cls, type: str) -> RegisteredNode: ...     # 缺失抛 NodeNotFoundError

    @classmethod
    def all(cls) -> dict[str, RegisteredNode]: ...

    @classmethod
    def unregister(cls, type: str) -> None: ...        # 测试专用, 不在公开 API
```

**测试隔离**: `tests/conftest.py` 加 fixture `node_registry_isolate`, 测试结束 unregister 当次注册的 type, 防互相污染。**不用** "_clear_registry_for_tests" (因为生产代码也会注册节点)。

## 5. WorkflowRunner (`runner.py`)

### 5.1 公开 API

```python
class WorkflowRunner:
    def __init__(self, store: ArtifactStore, run_log_root: Path): ...

    def run(self, workflow: Workflow | dict | str | Path) -> RunResult:
        """同步运行整个 workflow, 返回 RunResult.
        - workflow 可以是 Workflow 实例 / dict / JSON 字符串 / 文件路径,
          内部统一 Workflow.model_validate.
        """
```

### 5.2 执行步骤 (固定顺序)

1. **加载**: 接收四种入参形态 (Workflow / dict / JSON str / Path), 统一成 `Workflow`。
2. **校验**:
   - 每个 `node.type` 必须在 `NodeRegistry` 中。
   - 每条 `inputs` / `edges` 端点形如 `"<node_id>.<port>"`, `node_id` 必须存在。
   - 不存在悬空依赖。
3. **构建 DAG + 拓扑排序 + 查环**:
   - 合并 `node.inputs` 和 `edges`, 统一成 `dict[node_id, set[upstream_node_id]]`。
   - 标准 Kahn 算法, 若残余节点 > 0 抛 `CycleError(cycle_nodes=[...])`。
4. **生成 run_id**: UUID4 hex。**先** mkdir `workflow_runs/<run_id>/nodes/`, **再** 写入 `workflow.json` (用户传 dict 也能复原)。
5. **按拓扑顺序串行执行**, 每节点:
   - a. 收集 inputs: 从已完成节点的 artifact 里读 (按 `node.inputs` 的 `"<upstream>.<port>"` 解析)。
   - b. 收集 params: `node.params`, 若 `params_model != None` 先校验。
   - c. **计算 input hash**: `sha256(json.dumps({"params": ..., "inputs_uris": ...}, sort_keys=True)).hexdigest()[:16]`。**注意 inputs 取 URI 不取内容** (大 DataFrame 不能塞 hash 里, 内容相同 URI 也会变, Phase 0 这样够用; Phase 1 再考虑内容指纹)。
   - d. 写 NodeRun(PENDING -> RUNNING) 到 run log。
   - e. 调 `registered.compute(params=params, inputs=inputs)`, try/except 包住。
   - f. **成功**: 用 `outputs_model` 校验 (若有), 写 artifact (按类型选 json/parquet), 写 NodeRun(SUCCESS)。
   - g. **失败**: 捕获异常, 写 NodeRun(FAILED, error=`f"{type(e).__name__}: {e}"`), **不抛**, 把现场 (params+inputs URI+traceback) 写到 `nodes/<node_id>/error.txt`, 标记本节点和**所有下游节点** SKIPPED, 继续到下一个独立分支或退出循环。
6. **汇总**: 任意 FAILED -> `RunResult.status = FAILED`, 否则 SUCCESS。返回 `RunResult`。

### 5.3 失败不抛全局的理由

Phase 0 也是 Phase 1+ 的契约: 一条 workflow 里可能有十几个节点, 跑到第 5 个挂了不应该让前 4 个也丢现场 (artifact 已写盘没影响, 但用户调试要的是"看到第 5 个挂了, 看到 6/7 是 SKIPPED 因为依赖它, 看到 8 是独立分支跑成功了")。runner 把"挂"作为结果之一返回, 不作为异常。

## 6. ArtifactStore (`artifacts.py`)

### 6.1 API

```python
class ArtifactStore:
    def __init__(self, root: Path): ...   # workflow_runs/ 父目录

    def run_dir(self, run_id: str) -> Path: ...
    def node_dir(self, run_id: str, node_id: str) -> Path: ...

    def write(
        self,
        run_id: str,
        node_id: str,
        output_name: str,            # "output" 是默认; Phase 1 节点可能多输出
        payload: Any,
    ) -> str:
        """根据 payload 类型自动选格式, 返回 URI (相对 root 的 POSIX path)."""

    def read(self, uri: str) -> Any:
        """反向: 按扩展名路由 -> pd.read_parquet 或 json.load."""
```

### 6.2 类型路由表

| payload 类型 | 落盘格式 | 文件名 |
|---|---|---|
| `pd.DataFrame` | `parquet` (pyarrow engine) | `<output_name>.parquet` |
| `dict` / `list` / `str` / `int` / `float` / `bool` / `None` | `json` (`ensure_ascii=False`, `indent=2`) | `<output_name>.json` |
| `pd.Series` | 先 `.to_frame()` 再走 parquet | `<output_name>.parquet` |
| 其它 (`np.ndarray` 等) | **抛 `TypeError`**, 节点要自己转成上述类型 | — |

### 6.3 目录约定

```
<root>/workflow_runs/<run_id>/
  workflow.json                       # 原始 workflow 副本
  run_log.jsonl                       # NodeRun 列表
  nodes/
    <node_id>/
      output.json | output.parquet
      error.txt                       # 仅失败时
```

URI 用 POSIX 相对路径, 形如 `"workflow_runs/abc123/nodes/universe/output.json"`, 跨平台稳定。

## 7. RunLog (`run_log.py`)

### 7.1 API

```python
class RunLog:
    def __init__(self, path: Path): ...                      # run_log.jsonl 绝对路径

    def append(self, run: NodeRun) -> None:
        """原子追加一行 JSON. 用 model_dump(mode='json') 保证 enum 落字符串."""

    def read_all(self) -> list[NodeRun]: ...

    def latest_status(self, node_id: str) -> NodeStatus | None:
        """同一 node_id 多次出现 (PENDING->RUNNING->SUCCESS), 取最后一行."""
```

### 7.2 文件格式

JSONL, 一行一个 `NodeRun.model_dump(mode='json')`。`mode='json'` 保证 datetime / Enum 序列化成字符串, 跨进程读不出 Pydantic 类型反序列化问题。

## 8. Mock 节点 (`nodes_mock.py`)

**只供 Phase 0 测试用**, 不接任何真实数据源。三个节点串成最小 DAG: `universe -> zeros -> row_count`。

### 8.1 `data.constant_universe`

```python
class UniverseParams(BaseModel):
    codes: list[str] = Field(default_factory=lambda: ["SH600519", "SZ000858"])

class UniverseOutput(BaseModel):
    codes: list[str]
    n: int

@node(
    type="data.constant_universe",
    params_model=UniverseParams,
    outputs_model=UniverseOutput,
    risk="normal",
    pit=False,
)
def constant_universe(params: dict, inputs: dict) -> dict:
    return {"codes": params["codes"], "n": len(params["codes"])}
```

### 8.2 `factor.zeros`

输入: `universe` (上游 universe output)。输出: DataFrame `code, value=0.0`。

```python
class ZerosParams(BaseModel):
    pass

@node(type="factor.zeros", params_model=ZerosParams, risk="normal")
def zeros(params: dict, inputs: dict) -> pd.DataFrame:
    codes = inputs["universe"]["codes"]
    return pd.DataFrame({"code": codes, "value": [0.0] * len(codes)})
```

### 8.3 `eval.row_count`

输入: `frame` (上游 DataFrame)。输出: `{"rows": int, "cols": int}`。

```python
class RowCountOutput(BaseModel):
    rows: int
    cols: int

@node(type="eval.row_count", outputs_model=RowCountOutput, risk="normal")
def row_count(params: dict, inputs: dict) -> dict:
    df = inputs["frame"]
    return {"rows": int(len(df)), "cols": int(df.shape[1])}
```

## 9. 测试矩阵

每个测试文件目标 + 用例 (一句一条, 全部确定性, 不依赖网络/真实数据)。

### 9.1 `test_workflow_schema.py`

- `Workflow.model_validate` 接受合法 JSON dict。
- 缺 `nodes` 或 `nodes=[]` 报错。
- `Node.type` 缺失报错。
- `Edge` JSON 字段名 `from` (Python 属性 `from_`) 双向序列化往返。
- `NodeRun` round-trip: `model_validate(json.loads(node_run.model_dump_json())) == node_run`。

### 9.2 `test_node_registry.py`

- `@node(type="foo.bar")` 注册成功, `NodeRegistry.get("foo.bar")` 返回对应 `RegisteredNode`。
- `@node()` 无 type 抛 `TypeError`/`AssertionError`。
- 同 type 重复注册抛 `ValueError`。
- `NodeRegistry.get("not.exist")` 抛 `NodeNotFoundError`。
- fixture `node_registry_isolate` 测试后干净。

### 9.3 `test_workflow_runner.py`

- 单节点 workflow 跑通, RunResult.status == SUCCESS。
- 链式 3 节点 (A->B->C) 拓扑顺序正确 (NodeRuns 时间戳递增)。
- 含环 workflow (A->B, B->A) runner.run 返回 status=FAILED + 报 CycleError 节点。
- 中间节点失败, 下游节点 status=SKIPPED, 上游 SUCCESS 不动。
- 缺失 `node.type` (registry 没注册) -> FAILED + 错误信息含 type 名。

### 9.4 `test_artifact_store.py`

- 写 dict -> 读出来 == 原 dict, 文件后缀 `.json`。
- 写 DataFrame -> 读出来 `df.equals(原)`, 文件后缀 `.parquet`。
- 写 Series -> 读回是 DataFrame (单列), 文件后缀 `.parquet`。
- 写 `np.ndarray` 抛 `TypeError`。
- URI 是 POSIX 路径 (Windows 上也是 `/`)。

### 9.5 `test_run_log.py`

- `append + read_all` 顺序保持。
- 同 node_id 多次 append, `latest_status` 取最后一条。
- Enum 落字符串 (`grep '"status": "success"' run_log.jsonl`)。
- 文件不存在时 `read_all` 返回 `[]`。

### 9.6 `test_workflow_e2e.py` (端到端)

构造下面 workflow JSON, `runner.run()` 跑完后断言:

```json
{
  "id": "wf_phase0_mock",
  "name": "Phase 0 mock 三节点",
  "version": 1,
  "nodes": [
    {"id": "universe", "type": "data.constant_universe",
     "params": {"codes": ["SH600519", "SZ000858", "SH601318"]}},
    {"id": "zeros", "type": "factor.zeros",
     "inputs": {"universe": "universe.output"}},
    {"id": "rowcount", "type": "eval.row_count",
     "inputs": {"frame": "zeros.output"}}
  ]
}
```

断言:
- `RunResult.status == SUCCESS`, `len(node_runs) == 3`, 顺序 universe -> zeros -> rowcount。
- `artifacts_root/workflow.json` 存在, JSON 内容等于输入。
- `artifacts_root/nodes/universe/output.json` 内容 `{"codes": [...], "n": 3}`。
- `artifacts_root/nodes/zeros/output.parquet` 读回是 3 行 2 列。
- `artifacts_root/nodes/rowcount/output.json` 内容 `{"rows": 3, "cols": 2}`。
- `run_log.jsonl` 共 6 行 (3 节点 × {RUNNING, SUCCESS}), `latest_status("rowcount") == SUCCESS`。

## 10. 跨模块依赖图 + 风险

```
schema.py  <- registry.py  <- runner.py
              ^
              |
nodes_mock.py (@node 引用 registry + schema)

artifacts.py <- runner.py
run_log.py   <- runner.py
hashing.py   <- runner.py
topo.py      <- runner.py
errors.py    <- 所有
```

**外部依赖**: Pydantic v2 / pandas 2.x / pyarrow / 标准库 (hashlib / json / uuid / pathlib / datetime / enum)。**零** 新增, 与计划书 §13 路径一致。

### 风险与防御

| 风险 | 防御 |
|---|---|
| 装饰器自动取 `__name__` 导致 refactor 破坏存储 workflow | 装饰器内 `assert type`, 测试 `test_node_registry.py` 覆盖 |
| 测试间 registry 污染 | fixture 隔离, 不用全局 clear |
| Windows 路径分隔符把 URI 弄成 `\` | `as_posix()` 统一; `test_artifact_store.py` 用例覆盖 |
| pandas 2.x parquet engine 缺 pyarrow | core 已声明; CI 装好就有, 不靠 fallback |
| `Edge.from` 是 Python 关键字 | Pydantic `Field(alias="from")` + `model_config = ConfigDict(populate_by_name=True)` |
| Pydantic Enum 序列化坑 | `model_dump(mode='json')` 强制 |
| 大 DataFrame 进 input hash 太慢 | hash 取 URI 不取内容, Phase 0 妥协, 写在文档里 |
| 失败节点把整批拖死 | 失败隔离 (§5.3), 下游 SKIPPED 但独立分支照跑 |

## 11. 验收清单 (DoD)

跑 `cd /g/financial-analyst && python -m pytest tests/test_workflow_schema.py tests/test_node_registry.py tests/test_workflow_runner.py tests/test_artifact_store.py tests/test_run_log.py tests/test_workflow_e2e.py -v` 全绿:

- [ ] schema: 5 用例。
- [ ] registry: 5 用例。
- [ ] runner: 5 用例 (单节点 / 链式 / 环 / 失败隔离 / 缺 type)。
- [ ] artifact: 5 用例 (dict / DataFrame / Series / ndarray 拒绝 / URI POSIX)。
- [ ] run_log: 4 用例。
- [ ] e2e: 1 用例 + 6 子断言。

同时:
- [ ] `python -c "from financial_analyst.workflow import WorkflowRunner, NodeRegistry, node, ArtifactStore"` 不报错。
- [ ] 用户能手写 workflow JSON 文件, `runner.run(path)` 跑通。
- [ ] `workflow_runs/<run_id>/` 目录结构与 §6.3 一致。
- [ ] 失败 workflow 留 `error.txt`, 现场可看。

## 12. 与计划书对齐表

| 计划书条目 | Phase 0 交付 | 备注 |
|---|---|---|
| §3.1 量化流程 DAG | Pydantic schema + runner topo 跑通 | 节点真实实现归 Phase 1 |
| §6.1 Workflow Schema | `schema.py` 全部字段实现 | yaml 输入留 Phase 1 (Phase 0 JSON 入口够) |
| §6.2 Node Registry | `@node` + 字符串 type + 14 类节点声明 | Phase 0 只注册 3 个 mock; 14 类是 Phase 1+ |
| §6.3 Workflow Runner | `WorkflowRunner.run` + topo + input hash + 失败隔离 | 重跑/断点续跑留 Phase 1 |
| §6.4 Artifact Store | json/parquet 双格式 + 目录约定 | 证据 artifact (单笔交易) 留 Phase 4 |
| §6.5 Gate Checker | **不做** | 整章留 Phase 3 |
| §12 Phase 0 验收 | 与 §11 完全对应 | |

## 13. Phase 1 接口预留 (不实现, 仅设计上让步)

为不让 Phase 0 卡 Phase 1, schema 中已预留:

- `Node.params` / `inputs` 是 dict, Phase 1 节点直接塞自己的字段, schema 不动。
- `RegisteredNode.pit` / `risk` 字段已存在, Phase 1 加 `GateChecker` 时读这两个字段。
- `Node` 留了 `meta` 字段, Phase 1 模板版本号 / 缓存键可往里塞, schema 不破坏。
- `ArtifactStore.write` 已支持 `output_name`, 多输出节点 (Phase 1 LGB rank 同时返回 score + rank) 调两次。
- `RunResult.artifacts_root` 是绝对路径, Phase 2 UI 直接挂这个目录就能展示。

## 14. 边界重申

- **不接** `G:/stocks` (§5.1)。Phase 0 mock 节点不访问 `stock_data/`, 不 import qlib。
- **不引** 新依赖。core 已声明的够用。
- **不做** PIT / Agent / Gate / UI / REST / 模板 / 复盘 / AI 代搭。
- **不 commit / push**。控制端统一处理。工作分支 `main`, 不动 `perf-backup` / `feat/etf-data-layer` / `etf-data-layer-wt`。
- **测试运行**: `cd /g/financial-analyst && python -m pytest ...`。Python = `D:\app\miniconda\python.exe` (pandas 2.3.3)。
