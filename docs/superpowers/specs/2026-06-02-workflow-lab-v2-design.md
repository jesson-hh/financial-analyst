# Workflow Lab v2 · 真节点 + Copilot + 信息密度 设计

> 状态: 设计审中
> 日期: 2026-06-02
> 子项目: QuantFlow Phase 2.5 — 让工作流实验室"真能用"
> 工作量: ~5 天 (SP-W2A 2.5 + SP-W2B 2 + SP-W2C 0.5)
> 借鉴 PandaAI panda_quantflow 5 件 (feature tag / 独立 Run model / terminate / paginated logs / NodeRegistry 群组), 避开 4 件 (MongoDB / RabbitMQ / Litegraph 画布 / 多用户 SaaS)

## 目标
当前 v1 工作流实验室只有 3 个 mock 节点 + 无 LLM, 用户反馈"逻辑不通顺/没法用". v2 让用户能:
1. **用真节点搭真工作流** (442 alpha + 用户 forge 因子 + 真 universe + 真 factor_report 输出)
2. **跟 AI 说自然语言** "用反转因子在 csi300 跑 IC", AI 出 workflow 草案 (引用经验库)
3. **看清楚每节点干啥** (tooltip/分组/输出预览, 不再"暂无信息")

## 范围

### 做
- **SP-W2A 真工作流节点** (5 个新节点 + feature tag/group 装饰)
- **SP-W2B Workflow Copilot** (NL → workflow JSON + KnowledgeIndex 经验引用 + SSE 流)
- **SP-W2C UI 信息密度 + 冷启动修复** (tooltip / 输出预览 / 空态引导 / lazy import)

### 不做 (借鉴 PandaAI 但不抄)
- ❌ MongoDB / RabbitMQ (我们 dev tool 不 SaaS)
- ❌ Litegraph 拖拽画布 (沿用步骤式 list)
- ❌ 多用户 + uid header + 鉴权
- ❌ 静态 panda_web 前端 (babel-inline 继续工作)
- ❌ PandaAI 的 3 个代码 chat 助手 (factor/backtest/code 助手 — 形态是写代码不是搭流程, 我们做的是 workflow 编排)

---

## SP-W2A 真工作流节点 (~2.5 天)

### 5 个新节点 (在 `factors/workflow_nodes/` 新模块)

| 节点 type | group | tag | params | inputs | output | 实现 |
|---|---|---|---|---|---|---|
| `data.universe` | data | data | `name: str` (csi300/csi500/csi800/csi_fast/csi300_active/all) | — | `list[str]` codes | 调 `resolve_universe_codes` |
| `factor.from_registry` | factor | factor | `name: str` | `panel: PanelData` | `pd.Series` alpha 值 | 调 `registry.get(name).compute(panel)`. 同时支持 442 alpha + UserFactorStore forge 因子 |
| `factor.from_expression` | factor | factor | `expr: str` | `panel: PanelData` | `pd.Series` | 调 `compile_factor(expr)` (DSL 白名单) |
| `data.load_panel` | data | data | `freq: str = "day"`, `start: str`, `end: str` | `codes: list[str]` | `PanelData` | 调 `PanelData.from_loader(get_default_loader(), codes, ...)` (含 panel_cache 复用) |
| `eval.factor_report` | eval | factor | `fwd_days: int = 5`, `n_groups: int = 10`, `cost_bps: float = 0` | `alpha: pd.Series`, `panel: PanelData` | `dict` (FactorReport asdict) | 调 `build_report(panel, lambda p: alpha, config, label, family)` (alpha 已算好则用 lambda 直返) |

### Feature tag 装饰器扩展
现有 `@node(type, params_model, outputs_model, risk='normal', pit=False)` 加 2 个字段:
- `group: str` ('data' | 'factor' | 'eval' | 'agent' | 'risk' | 'execution' | 'review' — 借 plan §6.2)
- `tag: list[str]` (['backtest', 'factor', 'signal', 'trade'] — 借 PandaAI FeatureTag, 多 tag)

Registry 新增 `list_by_group()` + `list_by_tag()` 接口供前端 + Copilot 用.

### 不动 v1 mock 节点
`data.constant_universe` / `factor.zeros` / `eval.row_count` 保留 (demo seed 用), 但标 `group='demo'` 工具栏可隐藏.

### 默认 EvalConfig
`eval.factor_report` 内部默认 `EvalConfig(universe=passed_universe_name, freq='day', start='2024-12-01', end='2026-05-30')` (1.5 年, 真实数据). universe 名传 `data.universe` 节点的 `name` 参数. 用户可在 `eval.factor_report` params 里覆盖 fwd_days/n_groups.

### 测试
| 文件 | 覆盖 |
|---|---|
| `tests/test_workflow_nodes_real.py` | 5 个节点单测 (用合成 panel 或小池), 验 schema 合规 + 输出形态 |
| `tests/test_workflow_e2e_real.py` | 端到端 4 节点链: universe → load_panel → factor.from_registry(name='rev_20') → eval.factor_report. 用 csi_fast (~100 只) 真数据, 验 ~10s 内返完整 FactorReport |

### Backend 改动
`buddy/server.py` build_app:
- `GET /workflow/nodes` 已返 schema, 加 group + tag 字段
- 新 `GET /workflow/nodes/by-group?group=factor` (前端工具栏分组用)
- 新 `GET /workflow/factors/registry` 返 442 alpha + user_factors 名+简介 (供 Copilot 上下文 + UI 下拉)

---

## SP-W2B Workflow Copilot — NL → workflow JSON (~2 天)

### 核心创新 (PandaAI 没有这个!)
PandaAI 的 chat 助手是**代码助手** (写 Python). 我们的 Copilot 是**workflow 编排助手** (生成 DAG JSON).

### 新端点
```
POST /workflow/copilot/draft  (SSE stream)
  Request: {goal: str, universe?: str, freq?: str}
  
  Server 流程:
    1. 收集上下文:
       - NodeRegistry schemas (含 group/tag)
       - 442 alpha 名+简介 + user_factors 名+简介 (via /workflow/factors/registry)
       - KnowledgeIndex.search(goal, k=5) → 经验 chunks (含 source/section)
       - FDR-significant 因子白名单 (factor_insights.md 顶部段, SP-2 已建)
    2. 构造 LLM prompt:
       系统提示 = "你是 A 股量化工作流设计师, 把用户目标翻译成 workflow JSON.
                  必须用现有节点 + 引用经验时给出引用. 拒绝已证伪因子 (见 pitfalls)."
       用户消息 = goal + 上下文 packed
    3. LLM 流式调用 (qwen 默认, OPENAI/DEEPSEEK fallback per env var)
    4. SSE 推:
       - thought {text} (LLM 推理过程, 流式)
       - draft {workflow_json, cited_experiences, risk_flags, used_factors}
       - done {}
```

### Frontend Copilot 面板
quant.jsx 的 WorkflowLab 顶部加固定栏:
```
┌──────────────────────────────────────────────────┐
│ 🤖 让 AI 代搭: [_________用反转因子在csi300跑IC_] [Go] │
└──────────────────────────────────────────────────┘
[SSE 流式推理显示在临时 panel]
↓ 出 draft
┌─────────────────────────────────────┐
│ 草案:                                │
│ • universe(csi300_active)            │
│   ↓                                  │
│ • load_panel                         │
│   ↓                                  │
│ • factor.from_registry(name=rev_20)  │
│   ↓                                  │
│ • eval.factor_report                 │
│                                      │
│ 引用经验:                            │
│ • factor_insights.md §rev_20 历史    │
│ • pitfalls.md §游资博弈票排除        │
│                                      │
│ 风险提示:                            │
│ • 系统性下跌中反转因子失效           │
│                                      │
│ [✓ 用这个加载到画板] [✗ 重来]        │
└─────────────────────────────────────┘
```

### LLM 选择
- **默认 qwen** (`qwen3.5-plus`, 阿里云百炼)
- env `FA_COPILOT_LLM=qwen|openai|deepseek` 切换
- ⚠ **你 .env 里 qwen key 之前 401 过期, 必须刷** 否则 Copilot 走 401 fallback
- 复用现有 `LLMClient.for_agent("buddy")` 接口 (`financial_analyst/llm/client.py`)

### 测试
| 文件 | 覆盖 |
|---|---|
| `tests/test_workflow_copilot.py` | mock LLM (注入合法 JSON 输出), 验上下文构造 + SSE 事件顺序 + draft schema 合规 + KnowledgeIndex 引用 |
| 同上 | 验 LLM 输出非 JSON / 输出含禁用因子 → 错误处理 |
| 烟测 (mark slow) | 真 qwen 调一次, 验端到端 (默认 skip, 控制端手动跑) |

---

## SP-W2C UI 信息密度 + 冷启动修复 (~0.5 天)

### 修冷启动 (本轮 hot fix)
`build_app()` 启动期太重 → 第一次 `/factor/list` 超时 → 前端"暂无因子":
- mock_nodes import → **lazy** (第一次 `/workflow/nodes` 时再 import)
- demo seed → **lazy** (第一次 `/workflow` 列表时检查 + 写)
- chromadb / KnowledgeIndex 初始化 → **lazy** (第一次 Copilot 调用时)

### Tooltip + 节点信息
- 工具栏节点 hover → 浮层显示: 完整 description / inputs 形态 / outputs 形态 / risk / pit / tag
- AutoForm 上方加节点 metadata: type / group / 来源

### 输出预览
- SSE `node_done` 事件含 artifact_uri → 前端调 `/workflow/runs/{run_id}/artifacts/{node_id}` 拿真实输出
- 节点旁加 "📊 查看输出" 按钮 → modal 显示:
  - dict → 美化 JSON
  - DataFrame → 前 20 行 table
  - Series → 散点图 (小型 SVG)
  - 大对象 → 摘要 (shape + dtype + head 3)

### Demo workflow 说明
demo seed JSON 加 `description: "演示 3 个 mock 节点链路, 不接真数据..."`, UI 顶部显示.

### 空态引导
- "暂无因子" → "→ 切到 [因子库 & 详情] 查看 442 个 alpha"
- step list 空 → "👈 从左侧工具栏点节点添加 / 或上方让 AI 代搭"

---

## 跨切关注

### 提交策略
- 单分支 `feat/workflow-lab-v2` (从 main 派生, main 当前 = 4e185e2 假设已合)
- 单 commit (per pattern)
- 不推 origin (保留等一起推)
- 包含 SP-W2A + SP-W2B + SP-W2C 三件

### 不引新依赖
- LLM 已有 (`financial_analyst.llm.client.LLMClient`)
- KnowledgeIndex 已有 (SP-1)
- 因子 registry 已有
- panel_cache 已有

### Workflow 调度
单 workflow, 3 阶段串行 (避免 Phase 0 并行原罪):
1. **SP-W2A real-nodes** (1 agent): 5 节点 + group/tag + 2 测试
2. **SP-W2B copilot** (1 agent): /workflow/copilot/draft + frontend Copilot 面板 + 1 测试
3. **SP-W2C polish + verify** (1 agent): lazy import 修冷启动 + tooltip + 输出预览 + 空态 + 对抗式 DoD + Playwright 烟测

### 验收 DoD
- [ ] `GET /workflow/nodes` 返 8 节点 (3 mock + 5 real), 每个有 group + tag
- [ ] `GET /workflow/factors/registry` 返 442+ 因子名
- [ ] 端到端 (TestClient): universe(csi_fast) → load_panel → factor.from_registry(rev_20) → eval.factor_report → 返 真 FactorReport, rankIC 数字真实
- [ ] `POST /workflow/copilot/draft` mock LLM → SSE 流 + draft JSON 合规 + 含 KnowledgeIndex 引用
- [ ] Playwright: 切到工作流实验室 → 看到 Copilot 输入框 (顶部) + 8 节点工具栏 (按 group 分) → 输入"用反转因子在csi300跑IC" → 流式出推理 + draft → 点"用这个" 加载 → Run → 看到真 IC 报告 artifact
- [ ] 冷启动: 第一次 `/factor/list` < 5s (lazy import 修)
- [ ] 全量回归 1233+ → 1260+ 不破
- [ ] 工作分支 feat/workflow-lab-v2, main 不动
