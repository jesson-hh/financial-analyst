# Workflow 步骤式 DAG 编辑器 · 设计 (Phase 2 UI on Phase 0 backend)

> 状态: 设计审中, 待 user 确认后进 workflow
> 日期: 2026-06-02
> 子项目: QuantFlow Phase 2 — 按 plan §11.1 步骤式 DAG, **不做画布拖拽**
> 工作量: ~5 天 (后端 2 天 + 前端 2-3 天 + demo seed 0.5 天)

## 目标
让用户在 `quant.jsx` 里:
1. 从 NodeRegistry 看全部可用节点
2. 加节点到 step list, 表单填参数, save 成 workflow JSON
3. 点 Run → SSE 实时看节点状态 + log + artifact
4. 看运行历史, 重看任意 run 的 artifact

## 范围

### 做
- 后端 8 个 `/workflow/*` REST endpoints + SSE
- 前端 `quant.jsx` 第 5 模式 "工作流实验室"
- 1 个 demo workflow seed (3 mock 节点串好, 首次进入 UI 点 Run 就能跑完)
- DataPaths 加 `workflow_defs_root`

### 不做
- 拖拽画布 / ReactFlow (Phase 2.1+ 才考虑)
- 真量化节点接入 (PIT/LGB/FM/Agent/broker_sim — plan §12 Phase 1, 独立 7-10 天)
- AI Copilot 自然语言代搭 (plan Phase 3)
- 图表证据页 (plan Phase 4)
- 自由 edge 编辑 (默认 sequential, 第一版只支持线性链)
- 嵌套 object params (节点 params 都是平的 dict)
- 模板商城

---

## 后端 (financial_analyst/buddy/server.py)

### 8 个新 endpoints

#### 1) `GET /workflow/nodes`
返 NodeRegistry 全部节点 schema, 前端用来构造工具栏 + 参数表单.
```python
# 响应
{"nodes": [
  {"type": "data.constant_universe",
   "description": "Demo: 返回固定 codes 列表",
   "params_schema": {"type":"object", "properties": {"codes": {"type":"array", "items":{"type":"string"}}}},  # = params_model.model_json_schema()
   "outputs_schema": {...},
   "risk": "normal", "pit": False}
]}
```

#### 2) `POST /workflow/create`
请求体 = Workflow JSON. 流程: `Workflow.model_validate(req)` 校验 → 生成 `wf_id` (uuid4 取前 12 字符) → 写盘 `<workflow_defs_root>/{wf_id}.json` → 返 `{wf_id}`.

#### 3) `GET /workflow/{wf_id}`
从 `<workflow_defs_root>/{wf_id}.json` 读回完整 Workflow JSON.

#### 4) `GET /workflow`
列所有 workflow defs, 按 mtime desc, 返 `{workflows: [{wf_id, name, mtime, node_count}]}`.

#### 5) `POST /workflow/{wf_id}/run`
启 `WorkflowRunner.run()` (在 thread pool, 异步), 生成 `run_id` (uuid4 取前 12), 返 `{run_id}`. **实际 SSE 流由独立端点提供** (浏览器 EventSource 是 GET, 不能 POST).

#### 6) `GET /workflow/runs/{run_id}/stream` (SSE)
事件 (复用 buddy server 现有 `_sse(event, **data)` helper):
- `node_start` `{node_id, type, idx, n}`
- `node_done` `{node_id, status: success|failed|skipped, duration_ms, artifact_uri}`
- `workflow_done` `{run_id, status, n_success, n_failed, n_skipped, duration_ms}`
- `error` `{message}`

实现: 简单 `run_log.jsonl` tail (每 200ms `os.path.getsize` 比上次, 若变大 seek 读新行 → 解析 NodeRun → 推 SSE 事件). 不引入 watchdog 依赖.

#### 7) `GET /workflow/runs/{run_id}`
状态摘要: `{run_id, wf_id, status, started_at, ended_at, n_total, n_success, n_failed, n_skipped}`. 从 `workflow_runs/{run_id}/run_log.jsonl` 聚合.

#### 8) `GET /workflow/runs/{run_id}/logs` 和 `GET /workflow/runs/{run_id}/artifacts/{node_id}`
- logs: 返 `run_log.jsonl` 全部 NodeRun JSON list
- artifacts: 反序列化 ArtifactStore output (DataFrame → records, NaN → null via 现有 `_jsonable`)

#### 9) `GET /workflow/runs`
列最近 N (默认 20) runs, 扫 `workflow_runs/*/` 按 mtime desc.

### 存储路径
- workflow defs (新): `<workflow_defs_root>/{wf_id}.json`
- workflow runs (Phase 0 已建): `<store.root>/workflow_runs/{run_id}/...`

### DataPaths 扩展
`financial_analyst/data/paths.py`:
- 加 `workflow_defs_root_override: Optional[Path] = None`
- 加 `@property workflow_defs_root` (默认 `parquet_root.parent / "workflow_defs"`; env `FA_WORKFLOW_DEFS_ROOT` 覆盖)

### Server 集成
`build_app()` 内:
- 共享 `_workflow_store = ArtifactStore(<store_root>)` (在 `build_app` 级生命周期)
- `_workflow_runs: dict[str, RunStatus]` 内存登记 (server 重启即失忆, MVP 可接受)
- `POST /run` 用 `asyncio.to_thread` 跑 `WorkflowRunner(store, run_log_root).run(workflow)`

### 测试
- `tests/test_workflow_rest.py`: TestClient 验 8 端点 (mock NodeRegistry + tmp 路径)
  - GET nodes 返 schema dict
  - POST create 返 wf_id + 文件落盘
  - GET wf_id 读回原 JSON
  - POST run 返 run_id, 等到 SSE workflow_done
  - GET runs/{run_id} 状态正确
  - GET logs / artifacts 拿到内容
  - GET workflow 列含新建的
- `tests/test_workflow_sse.py`: 跑 3 mock 节点 workflow, 订阅 SSE, 验事件顺序 node_start ×3 + node_done ×3 + workflow_done ×1

---

## 前端 (financial_analyst/ui/quant.jsx)

### 改动
- 不动 POOLS / 4 现有模式按钮
- 加第 5 模式按钮 "工作流实验室" (label, 不加图标避免 emoji 依赖)
- 加新组件 `WorkflowLab` (在文件末尾)
- 顶层模式分发加 case `"工作流实验室"` → `<WorkflowLab />`
- `quant.html` bump `quant.jsx?v=20260602-2`

### `WorkflowLab` 布局
3 列 + 底部 log panel (用 grid 布局):
```
┌─节点工具栏──┬─Step List──────────┬─参数表单─┐
│ data       │ 1. constant_uni... │ codes:   │
│   • univ   │   ↓                │ [SH600519│
│ factor     │ 2. factor.zeros    │  SH600036│
│   • zeros  │   ↓                │ ]        │
│ eval       │ 3. eval.row_count  │          │
│   • row_c  │                    │ [Apply]  │
│             │ [Save] [Run]       │          │
└────────────┴────────────────────┴──────────┘
┌─运行日志 (SSE 实时)──────────────────────────┐
│ ▶ universe started (1/3)                    │
│ ✓ universe done · 12ms · artifact ↓         │
│ ▶ zeros started (2/3)                       │
│ ✓ zeros done · 8ms                          │
│ ▶ rowcount started (3/3)                    │
│ ✓ rowcount done · 5ms                       │
│ === workflow_done: 3 success ===            │
└──────────────────────────────────────────────┘
```

### React 状态 (useState in `WorkflowLab`)
- `nodes`: 从 `GET /workflow/nodes` 拉 (工具栏 source); useEffect 首次加载
- `currentWorkflow`: `{wf_id|null, name, nodes:[], edges:[]}`
- `selectedNodeIdx`: number|null
- `runId`: string|null
- `runEvents`: array of SSE events (累积)
- `runStatus`: idle|running|done|error

### 交互行为
| 动作 | 实现 |
|---|---|
| 点工具栏节点 | append to `currentWorkflow.nodes` (新 id = `${type.split('.')[1]}_${idx}`, params={}); 若上一节点存在自动加 edge (`output` → `<next default input>`, 第一版固定按节点 outputs/inputs 的第一项) |
| 点 step list 节点 | setSelectedNodeIdx |
| ↑ / ↓ | 数组交换 (i, i±1) + 重算 edges |
| × | 移除节点 + 关联 edges |
| 编辑参数 (AutoForm) | 修改 `currentWorkflow.nodes[i].params`; [Apply] 提交到 state (避免每键击 setState) |
| Save | POST /workflow/create → 拿 wf_id 写入 currentWorkflow |
| Run | POST /workflow/{wf_id}/run → 拿 run_id → 用 EventSource 订阅 `GET /workflow/runs/{run_id}/stream` |
| SSE 事件 | append to runEvents + 高亮对应 step list 节点 |

### `AutoForm` 小渲染器 (~100 行 JSX)
读 JSON Schema 渲染 input. 支持类型:
- `string` → `<input type="text">`
- `integer` / `number` → `<input type="number">`
- `boolean` → `<input type="checkbox">`
- `array` of `string` → `<textarea>` (一行一个 code)
- `enum` (anyOf with const) → `<select>`
- `object` → 递归 (第一版**不做嵌套**, 节点 params 全是平的; 若遇到抛 console.warn 显示 "unsupported nested form")

### 前端测试
无自动测 (复用 Playwright 烟测在控制端跑):
- 加载 `:5173/quant.html`, 切到 "工作流实验室"
- 工具栏出现 3 mock 节点 (从 `/workflow/nodes` 拉)
- 点击工具栏 `constant_universe` → step list 出现 1 节点 → 右栏 form 出现 codes 字段
- 类似加 2 更节点, save, run
- log 实时滚, 完成后 step list 节点高亮 success
- F5 重新加载, 历史列表里看到刚才的 run

---

## demo workflow seed (首次启动写一个)

`build_app()` 启动时, 若 `<workflow_defs_root>/` 下无文件, 写:
```json
{
  "id": "demo-mock-3-nodes",
  "name": "Demo: 3 mock 节点链路",
  "nodes": [
    {"id": "universe", "type": "data.constant_universe",
     "params": {"codes": ["SH600519", "SH600036"]}},
    {"id": "zeros", "type": "factor.zeros", "params": {}},
    {"id": "rowcount", "type": "eval.row_count", "params": {}}
  ],
  "edges": [
    {"from_node": "universe", "from_output": "output", "to_node": "zeros", "to_input": "universe"},
    {"from_node": "zeros", "from_output": "output", "to_node": "rowcount", "to_input": "frame"}
  ]
}
```

---

## 跨切关注

### 工作流 (Workflow 调度)
单 workflow, 3 阶段串行 (no parallel — Phase 0 教训):
1. **Backend**: 8 endpoints + SSE + DataPaths + tests
2. **Frontend**: WorkflowLab 组件 + AutoForm + quant.html bump
3. **Verify**: 对抗式核 DoD + Playwright 烟测 + 全量回归

### 提交策略
- 分支: `feat/workflow-lab-ui`
- 单 commit (Phase 0 也是单 commit)
- 不推 origin (按 "保留等一起推" 模式)

### 不引新依赖
- 后端: 用 stdlib (uuid, json, asyncio, threading) + 现有 FastAPI/SSE
- 前端: 自写 AutoForm 100 行, 不引 react-jsonschema-form

---

## 验收 DoD
- [ ] `GET /workflow/nodes` 返 3 mock 节点完整 schema
- [ ] `POST /workflow/create` 校验 + 落盘 `workflow_defs/{wf_id}.json`
- [ ] `POST /workflow/{wf_id}/run` + SSE 流 含 3 node_start + 3 node_done + 1 workflow_done
- [ ] `GET /workflow/runs/{run_id}` 状态正确, `/logs` 返 6 行 NodeRun, `/artifacts/{node_id}` 反序列化
- [ ] 浏览器 (Playwright) 切到 "工作流实验室" 看到 demo workflow, 改参数 + save + run + log 实时, 节点高亮 success
- [ ] 全量回归 1219+ → 1240+ (新增测试) 不破
- [ ] 工作分支 feat/workflow-lab-ui, main 不动
