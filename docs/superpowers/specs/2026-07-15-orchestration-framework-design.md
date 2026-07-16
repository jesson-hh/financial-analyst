# 编排框架 · Orchestrator-Workers + Evaluator-Optimizer — 设计文档 v1.1

日期:2026-07-15 · 状态:v1.1 设计收敛版(原四轮决策 + Lane 0 + 经验库不变;补齐两阶段规划、可验证 DAG、PIT/记忆防前视、sealed holdout、影子组合执行契约);待写实现计划(writing-plans)。

**参考来源**:
- HKUDS/Vibe-Trading — 静态 YAML DAG 编排(预设=依赖图)。
- TauricResearch/TradingAgents `@01477f9` — agent 名册、`dataflows/interface.py`(多源融合路由)、`agents/schemas.py`(结构化输出双表示)。
- simonlin1212/TradingAgents-astock `@e6b32a4` — A股 vendor 适配缝、3 个 A股原生 analyst、微结构提示词、`signal_data` 类。
- anthropics/financial-services `@4aa51ed` — `SKILL.md` 格式、`reader/critic/writer` 能力分档、不可信输入隔离、workers 不能再 spawn 子 agent。
- 既有帷幄/engine:`financial_analyst.agent.orchestrator.Orchestrator/DAGNode`、`agent.base.SubAgent/SubAgentResult`、`swarm.loader.load_preset`、`workflow/executor.py`(`run_graph` 确定性图)、`research/loop.py`、`autonomy/subagent.py`、`AgentMemory/memory_ops`、`console/curator.py`、`financial_analyst.backtest.Broker/VirtualPortfolio/CostModel/BacktestRunner`、`seats/`、`datafeed/`、`macro/market_tape/fundflow`。

---

## 0. 目标与决策记录

**目标**:抽出一个通用编排内核 `guanlan_v2/orchestration/`,把帷幄现有零散的多 agent 能力(复盘官五段、bull/bear 报告链、research 回路、rescore/rerank)统一成两个可组合的工作流——**Orchestrator-Workers**(中央 LLM 动态把任务拆成 worker 子任务并汇聚)与 **Evaluator-Optimizer**(生成→确定性求值→评估反馈→改进 的有界迭代)。同一内核由**落子(回测 replay)**与**帷幄(live/研究)**共用,只换数据绑定。

**用户拍板的决策**:
1. **范围**:先抽 `guanlan_v2/orchestration/` 通用内核,落子/帷幄 各写 adapter 后接(而非先绑某一具体业务)。
2. **执行器归属**:建立**独立于 `run_graph` 的 LLM-worker runtime**;以既有 `financial_analyst.agent.orchestrator.Orchestrator/DAGNode`、`financial_analyst.agent.base.SubAgent/SubAgentResult` 为行为基线抽 engine-neutral core/adapter,而非另造第三套依赖语义。确定性因子执行器 `workflow/executor.run_graph` 保持纯净不动,作为 worker/evaluator 调用的工具。
3. **反馈归因治理**:**确定性归因优先(边界门违反 + 引用链 + incomplete 节点)→ LLM 仅兜底**;责任 worker 自省(Reflexion)提炼教训;统一经**记忆 curator** 去重/校验/`[[链接]]`才入长期库。(避开 Who&When ICML'25 全自动归因 SOTA 仅 ~53% 的坑。)
4. **决定 1B · Lane D 出信号边界**:决策/风控车道只出 `ADVISORY_ONLY + SHADOW_ONLY` 工件与影子组合(原“非LLM信号”命名弃用,避免误解来源);不动真钱、不进实盘 signal/order bus。唯一成交者是确定性后端 Broker/clock;影子收益曲线专供 Evaluator-Optimizer 评估 LLM 决策质量。
5. **决定 2 · 辩论深度**:多空辩论 ≤2 轮、风控三席 ≤2 轮;仅 `dec.pm` 跑 `reasoner_deep`,辩手用 `fast`/`reasoner`(护住座席≤24 / token 闸)。
6. **决定 3 · 路由回落**:窄回落(仅 `RateLimitError`(及 `NotConfiguredError`)触发跨 vendor);同一 locale 的源冗余塞进 adapter 内部;A股独有的龙虎榜/北向/资金流/解禁单独成 `signal_data` 类并标 OPTIONAL(端点常坏 → 降级不 crash)。具体数据源获取实现**暂缓**,本期只定接口。
7. **决定 4 · 下游读法**:工件同时带结构化 `payload` + `rendered_md`,下游读 typed `payload`(不 regex 抠 markdown);现有 `reports/` 链分批迁进 `WorkerSpec`。
8. **Lane 0 市场情境层**:新增自上而下的市场级判断车道,内部**两段**——确定性 `market.factor`(因子 agent 算带参市场因子+走势)→ `market.regime`/`market.rotation`(LLM 读走势判断);产 regime 工件注入全局;判断走**经验库**(延迟标注 + 数值近邻类比检索 + 历史回放种子)。

**v1.1 收敛决策(本轮批准)**:
9. **两阶段规划**:固定、版本化的 `BootstrapPlan` 先跑 Lane 0,冻结 `ContextSnapshot`;动态 Orchestrator 只能基于该快照生成 `MainPlanDraft`,经校验/审批/freeze 后才成为可执行 `MainPlan`。不再让同一 Plan 同时承担“先算 regime”与“按 regime 选 worker”两个互相依赖的职责。
10. **复用而非第三套调度器**:新 `orchestration/dag.py` 是 engine-neutral 的严格运行内核/adapter,复用既有 `financial_analyst.agent.orchestrator.Orchestrator/DAGNode` 的软硬依赖、波次执行与**协程级**取消传播语义,以及 `agent.base.SubAgent/SubAgentResult` 的 Pydantic 输出语义;有界并发、稳定排序、持久状态、外部取消与 commit lease 属于新内核能力。`run_graph` 继续只做确定性计算图。不得长期维护第三套含义不同的 worker DAG。
11. **Plan 审批**:v1 动态 `MainPlan` 默认 `approval_policy=required`;版本化静态 preset 可 `auto`;审批事件必须绑定不可变 `plan_digest`。Orchestrator 失败时只有调用方在预先持久化的 `OrchestrationRequest` 给定 `fallback_preset_id` 才可回落,绝不由模型或运行时静默挑一个 preset。
12. **typed + event-sourced 运行契约**:`PlanNode/Dependency/NodeRun/DataResult/Artifact` 全部版本化;每层读取冻结输入快照;并发完成顺序不得改变最终工件;节点/工件事件先持久化再通知。
13. **PIT 覆盖数据与记忆**:PIT 判定一律看“当时可知时间” `available_at`,不看业务期末;存储查询先下推 `available_at <= as_of`,replay 快照只能含当时可见的 data、case、realized、lesson 和 past_context。adapter/raw candidate 若仍交来未来数据/未来记忆则 `FutureDataRefused`,不回落、不静默删除。
14. **Optimizer 隔离最终测试**:Optimizer 只看 train/validation/walk-forward;候选冻结后 sealed holdout 只开一次且结果不回流 improve/归因/记忆。跨 run 的试验数进入 append-only `TrialLedger`,不能换 `run_id` 清零窥视预算。
15. **影子组合后端正典**:影子执行消费结构化 `TargetPortfolioIntent`,以既有 `financial_analyst.backtest.Broker/VirtualPortfolio/CostModel/limit helpers` 为确定性成交基线,复用 `BacktestRunner` 的时钟与结果汇总,沿用既有 T+1、停牌、涨跌停、费用、滑点与手数语义,并补齐完整公司行动账本、止盈及最长持有期等缺口;前端 `runBacktest` 仅作兼容展示与镜像测试,不再是 Python adapter 的调用契约。

**非目标**:① 具体数据源的抓取实现(本期只定接口,复用 `datafeed/live_client`);② 让 LLM 直接下单/动实盘信号(永远 advisory);③ 改动 `run_graph` 核心;④ 新前端页面(UI 只填充不重建)。

**红线(贯穿)**:达标产物一律 draft、采纳永远人审;每个承重数字可溯源否则 `[UNSOURCED]`;worker 编数/违反其 `EvidencePolicy`/空产出 → `incomplete`;合法无工具 worker 不因零调用被误杀;LLM 零买卖,唯确定性 shadow/backtest clock 执行,影子不动真钱;**绝不静默回落到未配置 vendor**;PIT 存储查询先做 cutoff,任何试图进入 replay 候选集/快照且 `available_at > as_of` 的数据或记忆都**物理拒绝**(`FutureDataRefused`,绝不触发回落);绝不自改代码/提示词/skill,只能提 proposal;降级须标徽章、绝不冒充真。

---

## 1. 模块布局 `guanlan_v2/orchestration/`(新)

| 文件 | 单一职责 |
|---|---|
| `context.py` | `RunContext`(run/budget/cancel)+ 不可变 `DataContext`(as_of/clock/mode/calendar)+ `ContextSnapshot` 引用;prompt context 与 data context 分离 |
| `bootstrap.py` | 固定、版本化 `BootstrapPlan`:Lane 0 → `ContextSnapshot`;不由动态 Orchestrator 生成 |
| `spec.py` | `OrchestrationRequest` / `WorkerSpec` / `PlanDraft` / `PlanNode` / `Dependency` / frozen `Plan` / `GateCfg` / `DebateCfg` / evidence policy |
| `catalog.py` | worker 能力目录(Orchestrator 选子集的来源)+ skill 索引 |
| `dag.py` | engine-neutral worker DAG 内核/adapter(严格无环校验 · 软硬依赖 · 分层屏障 · 有界并发 · 预算预留 · 取消传播) |
| `orchestrator.py` | `OrchestrationRequest` + `ContextSnapshot` + 目录 → 候选 `MainPlanDraft`;校验、digest-bound 人审、冻结;仅 request 中显式 `fallback_preset_id` 可回落 |
| `events.py` | append-only `RunEvent`/`NodeRun`/`TrialLedger`;先持久化再发布,支持幂等恢复与审计回放 |
| `optimize.py` | 通用 Evaluator-Optimizer(泛化 `research/loop`;validation 回路 + sealed holdout + 停滞守卫) |
| `evaluator.py` | 四层 Evaluator(诚实闸→确定性指标→过拟合治理→归因反馈) |
| `governor.py` | 过拟合治理(窥视预算 / Deflated Sharpe / PBO / 复杂度罚 / walk-forward) |
| `honesty.py` | 诚实脊柱:`classify_worker`(incomplete 判定)、数字溯源校验、徽章 |
| `pool.py` | `ArtifactPool`=持久事件日志的 run 级索引视图;冻结 `InputSnapshot`、唯一写槽/确定性 reducer、发布/订阅 |
| `schemas.py` | `NodeRun` / `Artifact` / `Provenance` / `NumberAnchor` / `DataResult` / 结构化 payload / shadow intent |
| `memory/` | `store.py`=现有 memory 的统一 facade(不是第三套 store);`attribution.py`;`experience.py`(PIT-safe `RegimeCase`) |
| `data/` | `symbols.py` / `errors.py` / `result.py` / `source.py` / `registry.py` / `reader.py` / `render.py`(接口层,获取暂缓) |
| `market/` | `factors.py`(市场因子计算,走 `run_graph`/PIT 口径) |
| `adapters/` | `luozi.py`(后端 ShadowBacktestRunner/ShadowDecisionAgent)、`weiwo.py`(live/研究)、旧 schema/agent 映射 |

**复用边界(不重造、不混责)**:

- `financial_analyst.agent.orchestrator.Orchestrator/DAGNode`、`agent.base.SubAgent/SubAgentResult`、`swarm.loader.load_preset`:作为 worker DAG 的行为基线;复用 loader 的旧 YAML 实例化路径,新 catalog 负责实例化版本化 `PlanNode`;新内核补严格 validator、有界并发、稳定顺序、状态/事件/预算/恢复。现有 `set` 组 wave + 整波无界 `gather` 不是确定性排序或并发上限契约。若消除反向依赖,先抽 engine-neutral core,再让旧/新入口共同调用。
- `workflow/executor.run_graph` / `graph_signature`:只做同步、无 LLM 的确定性计算和候选签名。现有 `topo_order` 遇环会追加剩余节点,**不得**用作 worker Plan validator;`run_graph` 也不得反向调用 worker/Pool/模型。
- `autonomy/subagent.run_section_agent`:保留隔离 brief、工具白名单、seat、超时、confirm 自拒;外层补 typed 输出、token/model/prompt/skill digest、真实工具调用记录和取消结果。
- `research/loop` / `research/store`:抽出 propose→evaluate→critique→revise、停滞守卫、best candidate、append-only 轮次状态机;现有因子研究成为 adapter。
- 既有 CPCV/purge/embargo/Deflated Sharpe/CSCV/PBO:抽成无 Web/API 依赖的纯统计函数供 `governor.py`,不复制算法。
- 现有 `AgentMemory`/FTS/own/shared/borrowed + `memory_ops` proposal/人审/审计 + console global/session/keyed memory:由新 `memory/store.py` 统一 facade;不创建第三套持久化。
- `financial_analyst.backtest.Broker/VirtualPortfolio/CostModel/limit helpers` 是现有确定性成交基线;复用 `BacktestRunner` 的 clock/结果汇总,但不复用其逐日调用 `agent.decide()` 的非纯执行入口。通过无 LLM 的 `ShadowDecisionAgent` 或专用 `ShadowBacktestRunner` 消费冻结 intent。浏览器 `ui/seats/luozi-data.jsx::runBacktest` 不是 Python 可调用接口。
- `factorlib` draft、`datafeed/live_client`、既有 regime/市场温度/主线/industry lesson、数字 claim audit/introspector:继续复用并加 typed adapter。

---

## 2. 两大工作流

### 2.0 统一运行序列(先上下文、后规划)

`OrchestrationRequest → BootstrapPlan(固定 Lane 0)→ ContextSnapshot → Orchestrator 生成 MainPlanDraft → strict validate → 动态 Plan 人审/静态 preset 自动 → freeze(plan_digest)→ DAG workers → sink candidate → L0 诚实闸`。随后按 request 的 `workflow` 分支:开放研究走 `orchestrate_only → draft 人审`;需要定量优化才走 `validation 求值/优化 → sealed holdout gateway → draft 人审`。`optimize_existing` 是独立入口:必须同时给出 `existing_candidate_artifact_id/hash` 与其冻结 `existing_context_snapshot_id`,校验一致后直接从 L0/validation 开始,跳过 Bootstrap/Orchestrator;缺任一引用即拒绝,不静默重算。两个工作流可组合,不是每份报告都被强制回测/holdout。

- `BootstrapPlan` 本身也是冻结、可审计的静态 Plan,只含 `market.factor`(确定性)→ `market.regime/market.rotation`(LLM)。若可选市场数据缺失,产显式 `unknown/degraded` ContextSnapshot;不让动态 Planner 猜 regime。
- bootstrap 使用 `context_snapshot_id=None` 的不可变 RunContext;ContextSnapshot 提交后派生新的 main RunContext 引用它,不在原对象上补写字段。
- `ContextSnapshot` 只存不可变 artifact/data/memory 引用及 digest,不把可变 `run_preamble` 塞回 `DataContext`;任一 main worker 看到的是同一份快照。
- `MainPlanDraft` 冻结前校验:node instance 唯一、worker/catalog 存在、DAG 无环、所有 sink 可达、input/output schema 匹配、同槽写策略、mode/工具/skill/预算合法。动态 Plan v1 默认人审;审核后 plan 原地不可改。
- Orchestrator 只能选择 catalog 中的 worker ID 和受 catalog input model 限制的参数;不得生成任意 Python callable、文件路径、tool/MCP 名或 skill 路径。`fallback_preset_id` 属于调用前已持久化的 `OrchestrationRequest`,不依赖可能生成失败的 Plan;失败且 request 未显式给出即诚实终止。

### 2.1 Orchestrator-Workers(动态分解)

`ContextSnapshot + 任务/目标 → Orchestrator LLM(动态拆解 → 候选 MainPlanDraft)→ 校验/人审/冻结 MainPlan → 层内并行跑 worker → barrier 发布共享池 → Synthesizer(sink worker)汇聚 → 候选`。

- Orchestrator 从 `catalog.py` 挑 worker 子集(不是每次全 24 个),实例化 `PlanNode`,显式绑定 `Dependency`/slot/sink。已知重复任务可跳过 Orchestrator 走版本化 preset(便宜可复现,仿 Vibe),但仍过同一 validator。
- `dag.run(plan, run_ctx, pool)`:复用既有 async wave/软硬依赖语义;每层开始冻结 `InputSnapshot`,层内 bounded parallel,层末统一 barrier commit。下游只读上层已提交快照,同层 worker 互不可见。
- LLM worker brief = server-resolved `system_prompt` + 只读 typed 上游 payload + `ContextSnapshot` 引用;上游内容和工具结果均作为**不可信数据**分隔注入,不得把其中指令提升为系统指令。`rendered_md` 仅供人看,下游不 regex 抠 markdown。
- **依赖门控**:硬依赖只接受 Plan 声明的成功状态;软依赖缺失可令节点 `degraded` 后继续;`incomplete/failed/blocked` 不解锁硬依赖。`Dependency` 决定图上是否具备派发可能,`EvidencePolicy` 只能进一步收紧、不能放宽:若 dependency=DEGRADE 但 worker `optional_data_may_degrade=False`,派发前即 `BLOCKED(reason=EVIDENCE_POLICY_REQUIRES_INPUT)`;派发后数据才降级且违反 policy 则输出 `INCOMPLETE`;`FAILED` 只表示执行故障。
- 并发预算在派发前原子预留;runner 使用有界线程/协程池及 provider/model semaphore,不按 worker 数无限起 daemon thread。完成顺序不得决定 artifact_seq、reducer 输入顺序或 sink 上下文顺序。
- **结论 = sink worker 报告,无投票**;综合由那个末端 LLM 负责,引擎不算共识分。

### 2.2 Evaluator-Optimizer(迭代到指标)

`候选 → L0 诚实闸 → validation 确定性求值(run_graph/后端回测)→ L1/L2 指标与治理 → 达标?→ 否:L3 结构化归因反馈→ Optimizer 改进;是:冻结候选→ sealed holdout 一次→ draft 人审`。

```python
# optimize.py
def run_optimize(*, seed, ctx, study: StudySpec, split_spec, max_rounds, governor,
                 evaluate_validation: Callable[[Cand, DataContext], Metrics],
                 gate:                Callable[[Metrics], GateResult],
                 improve:             Callable[[Cand, Metrics, Feedback], Cand]
                 ) -> OptimizeResult: ...

# 与 Optimizer 进程/权限域分离;gateway 持有 sealed 数据与一次性 lease。
def finalize_candidate(*, optimized: OptimizeResult,
                       sealed_evaluator: SealedEvaluatorGateway) -> HoldoutReceipt: ...
```

- 泛化 `research/loop.run_research_loop`:保留**停滞守卫**(规范化候选签名相同→不重复求值,带警告重批一次仍同→诚实中断)、**诚实终止**、**draft-only** 与 append-only 轮次落档。
- 每个候选记录 `candidate_hash/parent_trial_id/data_snapshot_hash/split_spec_hash/code_prompt_model_hash`;validation 指标一旦揭示即写 `TrialLedger`。完全相同 candidate/data/split 可幂等复用旧结果,但不得借新 run 重新揭示更多指标。
- `family_id` 由 governor 根据 registry-resolved 研究目标、标签定义、universe、频率和 split policy 的规范化摘要派生;调用方显示名/自由文本不参与身份,不能换名字清零 trial/holdout 预算。调候选参数/图仍属于同一 family;只有上述研究身份实质改变才派生新 family,且必须记录 `parent_family_id/change_reason` lineage 与 governor attestation。
- Optimizer 只可修改候选 schema 明示的参数/图/结构化决策,绝不修改代码、prompt、skill 或 guardrail。它没有 holdout reader/tool;候选冻结后由独立 sealed-evaluator gateway 先原子预留 `(family_id,holdout_window_id)` lease 并写 TrialRecord,再开启窗口。结果存入受 capability 隔离的 sealed namespace;公共 event/TrialLedger 只见 opaque status/digest,普通 ArtifactPool/history/subscribe 无法解引用。holdout 指标不喂 `improve`、L3、归因或记忆;holdout 后再改是同 family 的新实验,只有等待后来成熟、与旧窗口不重叠的真正 OOT 数据才能获得新 holdout window,不能从现有历史立即再切一块续试。
- live 影子结果或 RegimeCase 尚未成熟时返回持久化 `WAITING_FOR_MATURITY + resume_after + wakeup_key`,不占着同步进程等待;幂等唤醒后只处理已成熟批次。
- `evaluate_validation/gate/improve` 可插拔:落子绑定确定性 shadow 后端,帷幄绑定 `run_graph`,Lane 0 绑定成熟 case 的 validation grader;重叠标签须 purge/embargo,调参采用 nested walk-forward。

### 2.3 影子组合(决定 1B)

Lane D 的 `dec.pm` 先产研究判断,`dec.trader` 只产 LLM 可写的结构化 `PortfolioTargetProposal`;runtime 校验后把它封装成 `TargetPortfolioIntent`。`intent_id/target_version/proposal_artifact_id+digest/source_decision_artifact_id/schedule id+version+digest/scheduled_for/decision_as_of/eligible_execution_at/valid_until/authority/execution_scope` 均由 runtime envelope 生成,模型不能自报或覆盖。Intent 必带 `origin=LLM`、`authority=ADVISORY_ONLY`、`execution_scope=SHADOW_ONLY`,**永不注册到实盘 order/signal bus**。不再使用含义相反的“非LLM信号”徽章。

- shadow executor 以 `Broker/VirtualPortfolio/CostModel/limit helpers` 为确定性成交基线,由无 LLM 的 `ShadowDecisionAgent` 或专用 `ShadowBacktestRunner` 消费冻结 intent;依据 schedule 的 bar frequency/execution policy 在 `eligible_execution_at` 对应可交易 bar 撮合,统一处理费用/滑点/T+1/涨跌停/停牌/手数/公司行动/止损止盈/最长持有。`(intent_id,scheduled_for,target_version)` 只用于“目标组合应用一次”;每个订单另以 `(target_apply_key,symbol,order_kind,trigger_bar,ordinal)` 生成 `order_id`,部分成交以 `(order_id,fill_seq)` 生成 `fill_id`。订单/fill/reject 都保留 causation key;看到 realized 后不得原地改 intent。
- 双曲线必须使用同一 universe、初始资金、数据快照、交易日历、成交/费用模型与 clock:①确定性策略;②LLM shadow intent。统一口径用于减少执行混杂,该对比**不自动构成因果归因**。
- 一次区间起点 intent 只能形成“单次决策持有曲线”。滚动影子曲线必须声明 `DecisionSchedule`(如每交易日盘前/每周/调仓日),在每个决策点用当时 PIT ContextSnapshot 重跑必要链;禁止整段结束后反向生成历史 intent。不是每 bar 都调用,但也不是用一次今天的判断覆盖整段历史。
- shadow 结果成熟后才能进反馈;未成熟 run 进入 `WAITING_FOR_MATURITY`,由 `resume_after/wakeup_key` 幂等恢复,且继续受 `TrialLedger`/sealed holdout 约束;人审也不能把同一个 shadow Artifact 原地提升为实盘指令。

---

## 3. Worker 目录(5 lanes + 跨切,24 个 spec / 运行时可多实例)

分档采用 FSI `reader / critic / writer` 表示分析能力,**不表示能否向 ArtifactPool 发布**;所有 worker 都可发布契约工件,只有 `can_emit_decision=True` 的 writer/sink 能出终局 advisory 决策。来源用 `borrowed_from:list[str]` 标注组合。**worker 不能再 spawn 子 agent(FSI 硬约束,仅一层委派)→ Lane D 辩论由顶层 runner 编排。**

### 3.0 Lane 0 · 市场情境层(自上而下,先跑,条件化全局)

Lane 0 由固定 `BootstrapPlan` 先跑。产出 ContextSnapshot 被四处消费:① Orchestrator 调 worker 配比(risk-off 多派风控);② 注入每个下游 worker;③ 喂 PM/仓位(**接现有市场温度护盾,做其显式上游**);④ Evaluator 按冻结 regime 分层报告收益/回撤/校准。Evaluator **不因“regime 标签错”而机械打折真实收益**,只做分层稳健性、regret 与校准分析。

| id | 段 | 角色 | tools/data | 输出 | 来源 |
|---|---|---|---|---|---|
| `market.factor` | ①确定性 | `ExecutionKind.DETERMINISTIC`:把原料算成带参市场因子+走势(见 §5),走 `run_graph`/PIT,不经 LLM 转述 | `market_tape`·`fundflow`·`macro`·指数/广度 | `market_factor_report`(因子走势向量+coverage) | 帷幄(regime 因子族 + market_tape) |
| `market.regime` | ②LLM | 读因子走势+经验库类比 → 趋势(牛/熊/震荡/unknown)× 风险(risk-on/off/neutral/unknown)× 热度(normal/overheat/unknown) | 读池 + `experience` | `regime_report`(结构化+概率/置信) | 帷幄 + 新 |
| `market.rotation` | ②LLM | 主线轮动判断:主线排序 + 阶段(启动/扩散/分化/退潮)+ 强度/持续性 | 读池 + 产业链框架 + `experience` | `rotation_report`(结构化) | 帷幄(fundflow/industry)+ 新 |

### 3.1 Lane A · 量化(5)

`quant.factor`(因子IC·帷幄 rescore/factor_ic)、`quant.model`(v4+DL集成·帷幄)、`quant.backtest`(vintage/OOS/PBO·帷幄 backtest cards)、`quant.fundamentals`(财报估值·**TA** + astock `get_profit_forecast`)、`quant.factor_miner`(小灶挖因子过 Sharpe/robust 门·帷幄 research/loop)。

### 3.2 Lane B · 量价几何(3,加深)

`pv.price_action`(15键确定性 PA + 可编辑方法论·帷幄 EV-017~026)、`pv.technical`(≤8 互补指标 + `get_verified_snapshot` 真值锚·**TA**)、`pv.microstructure`(五档/逐笔/炸板/主力·帷幄 live_book/market_tape/fundflow)。

### 3.3 Lane C · 文本(5)

`text.news`(快讯+全球·帷幄 kuaixun/news_marks~TA)、`text.sentiment`(**不调工具**、吃预取块、输出 band/score/confidence·**TA** 反捏造 #557/#796 + 帷幄 sentiment)、`text.research_report`(Kimi 研报抽取+旧报降权·帷幄)、`text.policy`(政策/窗口指导·**astock**)、`text.macro`(预测市场+打板温度·帷幄 macro + TA `get_prediction_markets`)。

### 3.4 Lane D · 决策/风控(6,有界辩论)

把现有线性报告链(bull/bear/辩护人/风控/写手)升级为**有界多轮辩论 + 轮次守卫**:`dec.bull`⇄`dec.bear`(≤2 轮,bear 晚一波逐条反驳)→ `dec.research_mgr`(裁多空 → 5 档评级 `ResearchPlan`·**TA**)→ `dec.risk_debate`(激进/稳健/中性三席,一个 spec 以不同 `PlanNode.id/round_role/debate_round/debate_turn` 实例化,≤2 轮)→ `dec.pm`(A股约束终裁 `PortfolioDecision`·deep 档·注入 PIT-safe past_context)→ `dec.trader`(只产 `PortfolioTargetProposal`;runtime 才封装 `TargetPortfolioIntent`,**仅 ADVISORY_ONLY + SHADOW_ONLY draft**)。

辩论消息不是并发修改一个可变 `DebateState`;每条消息是不可变事件 `(debate_id,round,turn,role)`,由 reducer 按 Plan 顺序重建视图。每一席每一轮均计一次 LLM invocation/seat 预算。

### 3.5 跨切(2)

`x.quality_gate`(数据质量 ABCDF·**astock**)、`x.number_critic`(**数字溯源门**:承重数字须溯源否则 `[UNSOURCED]`,拒捏造市值/价·帷幄 introspector + FSI 不可信输入隔离)。

### 3.6 skill 作者模型(FSI)

每个 skill 一个 `orchestration/skills/<name>/SKILL.md`:frontmatter 带 `name` + `description`(显式 "Perfect for / Not ideal for" 触发词),正文是开局 `## ⚠️ CRITICAL: Data Source Priority` 的清单式 playbook(= CoALA 程序记忆)。一份源 + `sync-skills.py` + `check.py` drift-lint 镜像进各 worker 包(单一事实源)。worker/Optimizer 只能提交 skill 修改 proposal,不得直接改源;每次运行记录 skill digest。

---

## 4. 多源数据融合接口(接口先行,获取暂缓)

借鉴 TA `dataflows/interface.py`:**双名间接**(agent 面方法名 ≠ vendor 适配名)+ `{method:{vendor:callable}}` 注册表 + config 回落链 + **按类型异常路由**;再补上 TA 没有、帷幄必须有的 typed `DataResult`、source attempt audit 与 `available_at` PIT。DataReader 全程返回结构,只有 `data/render.py` 把结果变成给 LLM 的不可信文本块。

### 4.1 Symbol 归一(A股缝)

```python
@dataclass(frozen=True)
class Symbol:
    code: str          # 6 位无前后缀 "600519"
    exchange: str      # SH | SZ | BJ
    board: str         # main | star | chinext | bj;具体号段由版本化表维护
    @property
    def dotted(self) -> str: ...      # "600519.SH"
    @property
    def engine_code(self) -> str: ... # "SH600519"(兼容现有仓库键)

class InstrumentMeta(BaseModel):
    symbol: Symbol
    is_st: bool | None = None          # 不能从代码纯语法推断;unknown 必须显形
    listed_at: datetime | None = None
    metadata_available_at: datetime | None = None

class LimitRule(BaseModel):
    pct: float | None                  # None=该时点无普通涨跌幅限制/规则未知
    reason: str
    rule_version: str

def normalize_symbol(raw: str) -> Symbol: ...
    # 纯语法(不联网):接受 bare/dotted/engine code;查版本化号段表;结果须 ^[0-9]{6}$
    # 才可拼进缓存键。只归一代码,不猜 ST、上市阶段或当日涨跌幅规则。
def resolve_name_to_code(raw: str, reader) -> Symbol: ...
    # 中文名/板块名 → 码;检测 CJK 查全市场名↔码表;拒行业/概念名(逼模型给 6 位码,绝不猜)
def resolve_limit_rule(sym: Symbol, as_of: datetime, meta: InstrumentMeta) -> LimitRule: ...
    # 结合板块、ST 元数据、上市日期和版本化规则;未知信息返回 unknown,不默认冒充 10%。
```

### 4.2 错误分类(路由的全部控制面)

```python
class DataError(Exception): ...
class NoDataError(DataError): ...       # 该 vendor 合法空返;窄回落下不跨 vendor
class StaleDataError(DataError): ...    # 超 method/category 专属 freshness policy;不跨 vendor
class RateLimitError(DataError): ...    # 记日志 + 换下一个
class NotConfiguredError(DataError, ValueError): ...
class FutureDataRefused(DataError): ... # PIT 硬停,永不回落(前视=泄漏=bug)
class MissingAvailabilityRefused(DataError): ... # strict PIT 缺 available_at,不能假定安全
class SourceBrokenError(DataError): ... # 协议/解析/认证等真实故障;大声失败,不换源掩盖
```

### 4.3 DataSource + 注册表 + 路由(窄回落)

```python
class SourceRegistry:                                   # == TA VENDOR_METHODS
    def register(self, method, vendor, impl) -> None: ...    # 加 vendor = 一行
    def resolve_chain(self, method, cfg) -> list[str]: ...   # 解析结果冻结进 DataContext
    def dispatch(self, method, req, ctx) -> DataResult:
        ...
        # for vendor in resolve_chain(...):
        #   try:
        #       raw=impl(req, ctx); res=ctx.pit_guard.check_raw(raw); record(success); return res
        #   except (RateLimitError,NotConfiguredError): record; continue  # 唯二跨-vendor回落
        #   except NoDataError:   return DataResult(status=NO_DATA,attempts=...)   # 不 continue
        #   except StaleDataError:return DataResult(status=STALE,attempts=...)     # 不 continue
        #   except FutureDataRefused: record; raise                               # 永不回落
        #   except DataError: record; raise                                       # broken primary 显形
        # chain 因 RateLimit/NotConfigured 耗尽:
        #   OPTIONAL category → UNAVAILABLE + DEGRADED_SOURCE badge;
        #   core category → raise 首个可重试错误(保留完整 attempts)
```

**决定 3 落地**:跨 vendor 回落**窄**(仅 `RateLimitError`/`NotConfiguredError`);`NoData/Stale` 明确终止当前 chain。同一 locale 的源冗余(mootdx→新浪、东财内部子端点)可塞进 adapter,但每个 subsource 尝试仍写 `SourceAttempt`,不能隐藏。vendor 级 token bucket/jitter/circuit state 由 registry 统一协调;并行 worker 共享请求去重/cache,不能各自起无限探针。

状态闭合:`resolve_chain()` 为空直接抛 `NotConfiguredError` 并记录 zero-config attempt;跨 vendor 后成功仍是 `OK + FALLBACK_USED` badge(来源变了但数据未必降质);`DEGRADED` 仅表示按 method policy 得到**部分可用**数据且 coverage 达最低门槛;无可用数据为 `NO_DATA/STALE/UNAVAILABLE`。任何降级都必须带原因、coverage 与 attempts,不能把 fallback 次数当质量分。

### 4.4 类别与注册(signal_data 独立可选)

```python
CATEGORIES = {
  "core_stock_apis": "a_stock",  "technical_indicators": "a_stock",
  "fundamental_data": "a_stock", "news_data": "a_stock",
  "signal_data": "a_stock",      # ★ A股独有,OPTIONAL:龙虎榜/北向/资金流/解禁/题材
  "macro_data": "polymarket_kalshi",  # OPTIONAL
  "prediction_markets": "polymarket", # OPTIONAL
}
OPTIONAL_CATEGORIES = {"signal_data", "macro_data", "prediction_markets"}
# signal_data: get_dragon_tiger_board / get_northbound_flow(自缓 CSV) /
#              get_fund_flow / get_lockup_expiry / get_hot_stocks
```

adapter 层(非 agent 层)编码异质性:GBK 编码(腾讯/同花顺)、各源市场前缀规则、Referer/Origin 头。非交易日/无数据在内核中是 typed status,不返回 `"N/A"` 字符串;`render_for_prompt` 才输出“非交易日/无数据,不得编造”。

### 4.5 DataReader facade

```python
class DataReader:                       # 构造时绑定不可变 DataContext
    def get_ohlcv(self, sym, start, end) -> DataResult[OHLCVRows]: ...
    def get_indicators(self, sym, indicator, curr_date, look_back_days=30) -> DataResult[IndicatorRows]: ...
    def get_verified_snapshot(self, sym, curr_date, look_back_days=30) -> DataResult[Snapshot]: ...
    def get_fundamentals(self, ticker, curr_date) -> DataResult[FundamentalRows]: ...
    def get_news(self, ticker, start, end) -> DataResult[NewsRows]: ...
    def get_signal(self, method, sym, curr_date) -> DataResult[SignalRows]: ...
    # 每个方法委托 registry.dispatch;确定性下游读 data,LLM 经 render_for_prompt(result) 读取。

def render_for_prompt(result: DataResult) -> str: ...
    # 包含 status/as_of/source/coverage/badges 的不可信数据块;不丢 attempts,绝不可脑补。
```

### 4.6 DataContext + PitGuard(TA 没有、帷幄必须有)

```python
class DataMode(Enum):
    ONLINE="online"; PIT_REPLAY="pit_replay"
class DataBackend(Enum):
    LIVE="live"; PIT_STORE="pit_store"; CACHE="cache"  # backend 不是时间安全模式

class ClockSpec(BaseModel):
    as_of: datetime; timezone: str; calendar_id: str; clock_version: str

class DataContext(BaseModel):
    schema_version: str = "1"
    as_of: datetime              # 带时区;run 起点冻结,ONLINE 也不是动态 today
    clock: ClockSpec
    mode: DataMode
    backend: DataBackend
    strict_pit: bool
    calendar_id: str
    resolved_vendor_chains: dict[str,list[str]]
    source_config_digest: str
    data_snapshot_id: str
    vintage_manifest_digest: str | None = None
    pit_guard: "PitGuard"

class RunBudget(BaseModel):             # 一次 run 唯一总账,覆盖 bootstrap/planner/main/repair/retry
    schema_version: str = "1"
    ledger_id: str
    max_tokens: int; max_llm_invocations: int; max_concurrency: int
    reserved_tokens: int = 0; reserved_llm_invocations: int = 0

class BudgetReservation(BaseModel):
    reservation_id: str; ledger_id: str; run_id: str
    scope_type: Literal["bootstrap","planner","plan","node","schema_repair","retry"]
    scope_id: str
    parent_reservation_id: str | None = None
    reserved_tokens: int; reserved_llm_invocations: int
    actual_tokens: int = 0; actual_llm_invocations: int = 0
    status: Literal["reserved","settled","released"]
    reserved_at: datetime; settled_at: datetime | None = None

class BudgetLedger:
    def reserve(self, scope_type, scope_id, tokens, invocations) -> BudgetReservation: ...
    def settle(self, reservation_id, actual_tokens, actual_invocations) -> BudgetReservation: ...
    def release(self, reservation_id, reason) -> BudgetReservation: ...

class RunContext(BaseModel):
    schema_version: str = "1"
    run_id: str
    data: DataContext
    context_snapshot_id: str | None = None # bootstrap 前为空;MainPlan 必须非空
    memory_snapshot_hash: str
    budget: RunBudget
    cancellation_token_id: str          # 状态变化写 RunCancelled/NodeStateChanged 事件
    replays_run_id: str | None = None

class PitGuard:
    # 比较 available_at(当时可知时间),不是 fiscal-period-end/effective_at。
    # PIT_REPLAY 强制 strict_pit=True。存储查询先下推 available_at<=as_of cutoff;
    # adapter/raw result 若仍交来 future row/vintage/memory → FutureDataRefused。
    # 多行结果要求每行/vintage 自带 available_at;缺失 → MissingAvailabilityRefused。
    # freshness 按 method/category + 交易日历配置,不用全局 MAX_STALE_DAYS。
    def check_raw(self, raw, *, request, attempts) -> DataResult: ...
```

`DataContext` validator:顶层 `as_of/calendar_id` 必须分别与 `ClockSpec.as_of/calendar_id` 完全一致,且时间都带时区;不一致直接拒绝。若 `mode=PIT_REPLAY`,则 `strict_pit=True`、`data_snapshot_id` 与 `vintage_manifest_digest` 均必填;snapshot manifest 固定每个 dataset/vendor 的 revision/content digest。缺 manifest 直接失败,不得临时读取当前 vendor/cache 后声称 replay。`ONLINE` 也冻结 run 起点 snapshot ID,但可按 freshness policy 读取启动时可见数据。

PIT 元数据最少含 `effective_at/available_at/ingested_at/revision_id/content_hash`;多行数据的**每行/每个 vintage**都必须有 `available_at`,顶层时间只作汇总审计。财报 revision 按 vintage 保存。缓存键至少含 `method/vendor/params/as_of/vintage/schema_version/source_config_digest`;每 run 冻结解析后的 vendor chain 以及 `data_snapshot_hash/memory_snapshot_hash/past_context_hash`。registry 后来新增 vendor 不得改变旧快照路由。

`PIT_STORE/CACHE` backend 可复用 `financial_analyst.data.paths.get_data_paths().pit_store_root` 所指向的 PIT store / 既有 `PitReader`,不能把 root 写死,且 backend 选择不能绕过 `DataMode.PIT_REPLAY + strict_pit`。现有 `PitReader` 会静默过滤未来行,而 `seats.news_marks._assemble_pit` 另有异常转空;仅在外层包装 `get_visible_info()` 已无法审计被删行,实现时须新增 raw/strict reader API 或在 `_load_jsonl_day` 原始记录层审计。还须修正 event 只按 `ann_date <= date`、probe 无行情时 `data_end` 回到日历末端的缺口。cache 若未通过 strict PIT 不得进入 replay、Evaluator、TrialLedger 或经验库训练。`news_coverage_floor` 与揭示墙是额外 coverage 门,不是 PitGuard 的替代品。

---

## 5. 市场因子(Lane 0 `market.factor` 的计算参数)

LLM 不看原始标量;`market.factor` 作为确定性 worker 先把原料算成**带参市场因子+走势**(时间序列),LLM 读走势判断。全部走 `run_graph`/PIT 口径,可回测;确定性 payload 不经 LLM 转述。

| 类 | 因子 | 计算 / 待设计参数 |
|---|---|---|
| 广度 | 涨跌家数比 AD | (涨−跌)/总;`MA5/MA20 + 斜率` |
| 广度 | 新高新低差 | NH−NL,窗口 `20/60日` |
| 广度 | 涨停强度/炸板率 | 涨停数 `3日EMA`;炸板率=炸板/(涨停+炸板) |
| 广度 | 连板高度/晋级率 | 最高连板 + 首板晋级率 |
| 广度 | **广度背离** ★ | `z(指数20日收益) − z(广度20日变化)`,`>阈值`=顶背离 |
| 资金 | 北向趋势 | `5/20日累计净额 + 斜率 + 250日分位` |
| 资金 | 主力净额分位 | 全市场主力净流入 `250日百分位` |
| 轮动 | 板块资金集中度 | `HHI` 或 top3 占比 |
| 轮动 | 主线扩散度 | 领涨题材内上涨个股广度 |
| 轮动 | 行业动量离散度 | 行业收益截面 `dispersion` |
| 波动/估值 | 已实现波动率 / 估值分位 | RV `20日`+短长比;指数 PE/PB `5年百分位` |
| 温度 | 打板温度 | 已有(market_tape) |

每个因子除数值外必须带 `factor_id/definition_version/params/universe/frequency/available_at/coverage/missing_policy/content_digest`;缺少历史覆盖的因子产 `UNAVAILABLE`,绝不补零或拿当前快照冒充历史。

每因子参数 = 窗口/平滑(MA/EMA)/标准化(z-score/百分位/截面 rank)/背离阈值/频率。**参数不拍脑袋**:用经验库已成熟 `realized` 做 train/validation IC、命中率与分层稳健性;标准化器只在查询时点之前的数据拟合并版本化。Evaluator-Optimizer 可调参,但 sealed holdout 不参与调参,所有揭示过的 validation trial 进入同一 `family_id` 的 TrialLedger。闭环:因子 agent 算 → LLM 解读 → 写 pending case → 延迟确定性标注 → validation 优化 → 冻结候选 → holdout 一次。

---

## 6. 记忆架构

### 6.1 短期 = 共享工件池 blackboard(`pool.py`)

run 级 blackboard,发布/订阅、不直接互调。**Artifact 不可变,Pool 是持久 RunEvent 日志的索引视图**:内存视图可随 run 释放,Plan/NodeRun/Artifact/关系事件必须持久保留,才能审计与回放。

- 每一 DAG 层开始冻结只读 `InputSnapshot`;同层 worker 互不可见。worker 输出先以 `ArtifactStaged` 持久化但保持不可见;staged 只进内部 journal,永不进入普通订阅/history。仅 `LayerCommitted` 事件能在一个事务中原子公开该层成功输出,崩溃时未过屏障的 staged 输出绝不进入下游快照。
- Plan 默认每 slot 单写者;多写必须显式声明确定性 reducer。reducer 输入按 `PlanNode.id` 排序,禁止按线程完成顺序。辩论消息同样按 `(debate_id,round,turn,role)` fold。
- event store 在每个 `(run_id,partition)` 为**所有**持久事件分配单调 `journal_seq`(含 staged,仅审计排序);commit 时另按 `(layer_index,PlanNode.id,output_key)` 规范排序分配公开 `artifact_seq`,并给可见事件分配 `visible_seq`。普通订阅只看 visible stream,按 `(partition,visible_seq,event_id)` 去重。线程完成时间和 staging 顺序不得改变 artifact_seq/reducer/sink 输入。
- LayerCoordinator 不接受调用方随意传 `staged_ids`:它从冻结 Plan、该层全部终态 NodeRun 与 winning commit lease 推导提交集,核对所有必需 `output_key` 后才能原子 commit。`LayerCommitted` 后关闭该层,任何迟到 stage 一律拒绝并记审计事件,不能永久遗漏后补输出。
- 逻辑幂等键=`(run_id,plan_digest,node_id,input_snapshot_hash,output_key)`;retry 有独立 attempt,同一逻辑键最多提交一个正式输出。重启从日志恢复 barrier、节点状态和已提交工件;staged-but-uncommitted 的 LLM attempt 可重跑并产生不同文本,但不能与旧 attempt 同时提交。

### 6.2 长期 = CoALA 三层(`memory/store.py`)

语义(事实/keyed 教训)、情景(过往 run/case)、程序(技能/配方/factorlib draft)。`memory/store.py` 是现有 `AgentMemory/MemoryIndex/memory_ops` 与 console memory 的统一 facade/索引,**不是第三套 store**。迁移期保留原格式、写入路径与 proposal/人审/审计链。

每条长期记录至少有 `created_at/available_at/valid_from/valid_to/review_state/content_digest`;worker 只能提交 pending/proposal,不能直接改核心记忆、skill 或程序记忆。检索必须先在存储层按 `available_at/valid_from/valid_to/review_state` 建立 PIT 可见集合,**再**做 role + recency/importance/relevance 排序与 top-k 截断,并冻结 `memory_snapshot_hash/past_context_hash`;先排序后过滤会让未来记录挤占名额。fallback 到全量记忆也必须先过同一可见性谓词。

### 6.3 反馈归因回路(`memory/attribution.py`,决定 3)

Evaluator feedback → **确定性归因(优先)**:① lane/gate 首个违反者 ② sink `input_refs/citations/supports/refutes` 采纳链 ③ incomplete/failed/blocked 节点。三条缩到 1–2 个 worker → **LLM 只在这 1–2 个里当裁判**。责任 worker Reflexion 只产 proposal → curator 做去重/链接/事实冲突/数字溯源/PIT 时间校验 → 人审或显式策略批准后落长期。sealed holdout 的指标与归因不得进入该回路。

### 6.4 经验库 RegimeCase(`memory/experience.py`,Lane 0 专用)

情景记忆的特化 + 类比检索,**延迟标注**闭环:

```python
class RegimeCase(BaseModel):
    id: str
    as_of: datetime                    # 判断时点,PIT 冻结
    available_at: datetime             # case 本身何时可检索
    feature_schema_version: str
    scaler_digest: str
    features: dict[str, float]          # 因子加工向量;缺失值/coverage 另记
    feature_coverage: dict[str,float]
    missing_features: list[str] = Field(default_factory=list)
    judgment: RegimeReport
    links: list[str] = Field(default_factory=list)
    content_digest: str

class CaseMatured(BaseModel):           # append-only event payload,不改 RegimeCase
    case_id: str
    realized: RealizedRegime
    matured_at: datetime
    available_at: datetime
    content_digest: str

class CaseReviewed(BaseModel):          # append-only event payload
    case_id: str
    maturity_event_id: str
    lesson: str
    reviewed_at: datetime
    available_at: datetime
    content_digest: str
```

生命周期:① 先按事件 `available_at <= as_of` 重建当时 case 视图,再取可见历史 case → ② 用**仅由查询时点之前样本拟合**的版本化 scaler 找 k 近邻 → ③ worker 判断 → ④ append `RegimeCase` pending 事实 → ⑤ N 个交易日后确定性实测并 append `CaseMatured`(复用 rerank matured / basket closes / vintage realized-date,不用 LLM)→ ⑥ validation 归因+Reflexion proposal → ⑦ curator/人审后 append `CaseReviewed`。pending/matured/reviewed 是事件折叠出的视图状态,绝不原地改旧对象。

- **类比检索 = 数值近邻**(标准化特征向量 cosine/距离),无需 embedding;但必须同 `feature_schema_version`,显式处理 missing/coverage,禁止全历史 scaler 偷看未来。
- **冷启动 = 按时间顺序历史回放种子**:features/realized 确定性计算,标签由前向收益/回撤派生(或 LLM 打标+人审)。查询较早日期时,后来的 case/realized/lesson 即使已批量生成也不可见;验收要求加入未来 case 后旧日期输出 hash 不变。

---

## 7. Evaluator 四层 + 过拟合治理

`evaluator.py` 四层(前三确定性,第四才用 LLM):
- **L0 诚实闸(求值前)**:不再要求“所有 worker 只能 completed”;按 Plan 检查所有硬依赖/sink 成功、允许的软依赖最多 degraded、payload schema/input refs/数字锚/执行边界均合法。`incomplete/failed/blocked` 命中必需链则拒评,避免先烧昂贵回测。
- **L1 validation 确定性指标**:`run_graph`/后端回测 → Sharpe·rank_ic·最大回撤·换手·胜率·尾部·coverage;按冻结 regime 分层报告,不改写真实收益。
- **L2 过拟合治理**(`governor.py`):purge/embargo + nested walk-forward/OOS validation + 从全局 TrialLedger 取原始试验数作为审计上界,再按统计实现对高度相关候选估计 `effective_n_trials` 供 Deflated Sharpe 使用 + 条件满足才算 PBO + 复杂度罚;样本/分割不足时返回 `unavailable`,不伪造治理分。超 trial/窥视预算即停。
- **L3 validation 归因反馈**:LLM 只读 validation 指标、曲线与显式 artifact 引用链,输出 `{target_worker_ids,evidence_artifact_ids,allowed_changes,reason,confidence}`;**不打分、不看 sealed holdout**。确定性归因无法把责任缩到 1–2 worker 时,诚实返回 ambiguous,不强判。

**sealed holdout**:候选通过 L0–L2 后先冻结 candidate hash。每个 `(governor-derived family_id,holdout_window_id)` 只允许为**一个**最终候选开启一次;失败、超时或 inconclusive 同样耗尽该窗口,不能换候选继续窥视。Optimizer 无 holdout 数据权限;独立 sealed-evaluator gateway 先原子写 reservation/一次性 lease/TrialRecord,再求值。详细指标/曲线只进 sealed result store,只有 final-report/human-review capability 能解引用;公共 RunEvent 只发布 `HoldoutReceipt(status,result_digest)`,不得把结果 artifact 放进普通 Pool。含指标的最终报告只存在 review namespace/ACL 视图,明确排除 Optimizer、后续 prompt、memory 与 ContextSnapshot。holdout 后改候选默认仍在原 family;只有研究目标/标签/universe/split policy 实质改变才可新 family 并保留 lineage。新窗口必须来自后来才成熟、与旧窗口不重叠的 OOT 数据,不得从已经存在的历史立即重切。

**TrialLedger**:append-only、跨 run/重启。每个已揭示 validation 指标的唯一 candidate/data/split 计一次 trial;完全相同三元组可复用缓存但不能多揭示指标。holdout 公共记录只含 `holdout_window_id/lease_state/status/result_digest/revealed_at/idempotency_key`,其 `metrics_revealed` 与 `validation_result_artifact_id` 必须为空;reservation 先于数据读取,失败/超时/inconclusive 统一原子转 `lease_state=exhausted`,重试只能幂等取回同一 receipt,不能再次开启。

---

## 8. Schema(字段级,`spec.py` / `schemas.py` / `pool.py`)

```python
# 字段级伪代码;实现使用 `from __future__ import annotations`。
T = TypeVar("T")

# ── 共享枚举 ─────────────────────────────────────────────────────────────
class PortfolioRating(str,Enum):
    BUY="Buy"; OVERWEIGHT="Overweight"; HOLD="Hold"; UNDERWEIGHT="Underweight"; SELL="Sell"
class SentimentBand(str,Enum):
    BULLISH="Bullish"; MILDLY_BULLISH="Mildly Bullish"; NEUTRAL="Neutral"; MIXED="Mixed"; MILDLY_BEARISH="Mildly Bearish"; BEARISH="Bearish"
class Tier(str,Enum): READER="reader"; CRITIC="critic"; WRITER="writer"
class Confidence(str,Enum): LOW="low"; MEDIUM="medium"; HIGH="high"
class ExecutionKind(str,Enum): LLM="llm"; DETERMINISTIC="deterministic"
class ToolCallRequirement(str,Enum): FORBIDDEN="forbidden"; OPTIONAL="optional"; REQUIRED="required"
class NodeStatus(str,Enum):
    PENDING="pending"; READY="ready"; RUNNING="running"; COMPLETED="completed"
    DEGRADED="degraded"; INCOMPLETE="incomplete"; FAILED="failed"
    BLOCKED="blocked"; SKIPPED="skipped"; CANCELLED="cancelled"
class ExperimentStatus(str,Enum):
    RUNNING="running"; WAITING_FOR_MATURITY="waiting_for_maturity"
    PASSED_VALIDATION="passed_validation"; SEALED_EVALUATING="sealed_evaluating"
    COMPLETED="completed"; FAILED="failed"
class DependencyPolicy(str,Enum): BLOCK="block"; DEGRADE="degrade"; SKIP="skip"
class PlanSource(str,Enum): BOOTSTRAP="bootstrap"; DYNAMIC="dynamic"; PRESET="preset"; PRESET_FALLBACK="preset_fallback"
class ApprovalPolicy(str,Enum): REQUIRED="required"; AUTO="auto"

class OrchestrationRequest(BaseModel):  # Plan 尚未生成时先持久化
    schema_version: str = "1"
    request_id: str; goal: str
    workflow: Literal["orchestrate_only","orchestrate_and_optimize","optimize_existing"]
    fallback_preset_id: str | None = None
    approval_policy: ApprovalPolicy = ApprovalPolicy.REQUIRED
    existing_candidate_artifact_id: str | None = None
    existing_candidate_hash: str | None = None
    existing_context_snapshot_id: str | None = None
    decision_schedule_id: str | None = None
    decision_schedule_version: str | None = None
    decision_schedule_digest: str | None = None

# ── Worker 目录契约 ──────────────────────────────────────────────────────
class ExecutionSpec(BaseModel):
    kind: ExecutionKind
    handler_ref: str | None = None       # deterministic handler;仅 catalog 解析,Planner 不能填写任意路径
    model_tier: Literal["fast","reasoner","reasoner_deep"] | None = None
    thinking_budget: int = 0

class EvidencePolicy(BaseModel):
    tool_calls: ToolCallRequirement = ToolCallRequirement.OPTIONAL
    require_input_refs: bool = True
    require_number_anchors: bool = True
    allow_unsourced_numbers: bool = False
    optional_data_may_degrade: bool = True

class WorkerSpec(BaseModel):
    schema_version: str = "1"
    id: str                              # catalog 稳定 id,如 "dec.pm"
    lane: Literal["market","quant","pv","text","decision","xcut"]
    persona: str
    system_prompt_ref: str               # catalog-owned;Plan 只引用 worker_id
    tier: Tier
    execution: ExecutionSpec
    can_emit_decision: bool = False
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    reads: list[str] = Field(default_factory=list)
    input_model: str; input_version: str = "1"
    outputs: dict[str,str]                # output_key → registry `Model@version`;至少含 primary
    evidence_policy: EvidencePolicy = Field(default_factory=EvidencePolicy)
    guardrails: list[str] = Field(default_factory=list)
    supported_modes: set[DataMode]       # catalog 必填;空集合非法
    borrowed_from: list[str] = Field(default_factory=list)

# ── 显式 DAG / Plan ──────────────────────────────────────────────────────
class Dependency(BaseModel):
    upstream_node_id: str
    artifact_slot: str
    upstream_output_key: str = "primary"
    inject_as: str
    policy: DependencyPolicy = DependencyPolicy.BLOCK
    accept_statuses: set[NodeStatus] = Field(
        default_factory=lambda: {NodeStatus.COMPLETED})

class PlanNode(BaseModel):
    id: str                              # Plan 内实例 id,如 risk.aggressive.r1
    worker_id: str                       # catalog WorkerSpec.id
    params: dict[str,Any] = Field(default_factory=dict) # 按 WorkerSpec catalog-owned input model 校验
    dependencies: list[Dependency] = Field(default_factory=list)
    writes_slot: str
    gate_ids: list[str] = Field(default_factory=list)
    debate_id: str | None = None
    round_role: str | None = None
    debate_round: int | None = None
    debate_turn: int | None = None
    condition: str | None = None         # 仅受限 DSL,禁止任意代码
    timeout_sec: int = 300
    max_attempts: int = 1
    token_reservation: int = 0

class GateCfg(BaseModel):
    id: str; metric: str; operator: Literal[">",">=","<","<=","=="]
    threshold: float | str; scope: str; blocking: bool = True
    unavailable_policy: Literal["fail","degrade","skip"] = "fail"
    min_samples: int | None = None
class GateResult(BaseModel):
    gate_id: str; metric_id: str
    status: Literal["passed","failed","unavailable"]
    observed: float | str | None = None; threshold: float | str
    blocking: bool; reason: str; metrics_artifact_id: str

class DebateCfg(BaseModel):
    id: str; seats: list[str]; turn_order: list[str]
    max_rounds: int; judge_node_id: str

class ReducerCfg(BaseModel):
    id: str; slot: str; reducer_id: str   # catalog-owned deterministic reducer
    producer_node_ids: list[str]
    output_model: str; output_version: str

class PlanDraft(BaseModel):              # Orchestrator/preset 候选,尚不可执行
    schema_version: str = "2"
    id: str; run_id: str; request_id: str; phase: Literal["bootstrap","main"]
    source: PlanSource; goal: str; as_of: datetime; mode: DataMode
    context_snapshot_id: str | None = None # bootstrap 必为空;main 必须引用已冻结 snapshot
    universe: list[Symbol]
    nodes: list[PlanNode]
    sink_node_ids: list[str]
    debates: list[DebateCfg] = Field(default_factory=list)
    gates: list[GateCfg] = Field(default_factory=list)
    reducers: list[ReducerCfg] = Field(default_factory=list)
    catalog_version: str
    catalog_digest: str
    approval_policy: ApprovalPolicy = ApprovalPolicy.REQUIRED
    budget_request_tokens: int = 0
    budget_request_llm_invocations: int = 0
    max_concurrency: int = 4            # 不能超过 RunBudget.max_concurrency
    stop_condition_ids: list[str] = Field(default_factory=list) # 仅 catalog/governor registry

class Plan(PlanDraft):                   # freeze 后的唯一可执行类型
    budget_reservation_id: str           # 从唯一 RunBudget 原子划拨,不是新额度
    frozen_at: datetime
    plan_digest: str

class NodeRun(BaseModel):
    schema_version: str = "1"
    node_run_id: str; run_id: str; plan_id: str; plan_digest: str; node_id: str; worker_id: str
    status: NodeStatus
    reason_code: str | None = None; reason: str | None = None
    attempt_id: str; attempt: int = 1
    input_snapshot_hash: str
    started_at: datetime | None = None; finished_at: datetime | None = None
    output_keys: list[str] = Field(default_factory=list)
    output_artifact_ids: list[str] = Field(default_factory=list)
    tool_call_count: int = 0; input_tokens: int = 0; output_tokens: int = 0
    warnings: list[str] = Field(default_factory=list)
    error_type: str | None = None

# ── Typed 数据结果 ───────────────────────────────────────────────────────
class DataStatus(str,Enum): OK="ok"; NO_DATA="no_data"; STALE="stale"; UNAVAILABLE="unavailable"; DEGRADED="degraded"
class SourceAttempt(BaseModel):
    vendor: str; subsource: str | None = None; configured: bool
    outcome: Literal["success","no_data","stale","rate_limited","not_configured",
                     "future_refused","missing_availability","error"]
    fallback_reason: str | None = None
    started_at: datetime; finished_at: datetime
class PitAudit(BaseModel):
    mode: DataMode; as_of: datetime
    rows_seen: int; rows_returned: int; future_rows: int; missing_available_at_rows: int
    guard_result: Literal["passed","filtered","refused"]
    latest_available_at: datetime | None = None
class PitRecord(BaseModel):               # 所有多行/多 vintage data item 的基类
    effective_at: datetime | None = None
    available_at: datetime
    ingested_at: datetime
    revision_id: str | None = None
    content_digest: str
class DataResult(BaseModel, Generic[T]):
    schema_version: str = "1"
    id: str; method: str; request_digest: str
    status: DataStatus; data: T | None
    coverage: float | None = Field(default=None,ge=0,le=1)
    degradation_reason: str | None = None
    vendor: str | None = None; subsource: str | None = None
    resolved_vendor_chain: list[str]
    source_config_digest: str
    effective_at: datetime | None = None
    available_at: datetime | None = None
    ingested_at: datetime | None = None
    fetched_at: datetime
    revision_id: str | None = None
    content_digest: str
    audit_digest: str                 # 含完整 attempts/fetched_at;与语义 content digest 分离
    row_time_metadata_digest: str | None = None # 多行结果每行 available_at 清单摘要
    attempts: list[SourceAttempt]
    pit_audit: PitAudit
    warnings: list[str] = Field(default_factory=list)
    badges: list[str] = Field(default_factory=list)

# ── Artifact / provenance ────────────────────────────────────────────────
class ArtifactRef(BaseModel):
    artifact_id: str; producer_node_id: str; slot: str; output_key: str; kind: str; content_digest: str
    relation: Literal["input","citation","supports","refutes"]
class ToolCallRecord(BaseModel):
    tool: str; request_digest: str; result_digest: str | None = None
    started_at: datetime; finished_at: datetime; status: str
class Provenance(BaseModel):
    run_id: str; plan_id: str; plan_digest: str; node_id: str; as_of: datetime; pit_mode: DataMode
    code_version: str
    provider: str | None = None; model: str | None = None; model_snapshot: str | None = None
    model_response_id: str | None = None; model_response_digest: str | None = None
    model_config_digest: str | None = None
    sampling_seed: int | None = None
    prompt_digest: str | None = None
    skill_digests: dict[str,str] = Field(default_factory=dict)
    data_result_ids: list[str] = Field(default_factory=list)
    tool_calls: list[ToolCallRecord] = Field(default_factory=list)
    fallback_events: list[str] = Field(default_factory=list)
class NumberAnchor(BaseModel):
    label: str; value: float; unit: str | None = None; as_of: datetime | None = None
    payload_path: str
    source_artifact_id: str | None = None
    source_data_result_id: str | None = None  # 两者皆空 ⇒ UNSOURCED
class Artifact(BaseModel):
    schema_version: str = "2"
    id: str; kind: str; slot: str; output_key: str; producer_node_id: str; run_id: str
    payload_type: str; payload_version: str
    payload: dict[str,Any]              # stage 时按 payload_type/version registry 强校验
    rendered_md: str                   # 人类展示;下游不解析它
    input_refs: list[ArtifactRef] = Field(default_factory=list)
    provenance: Provenance             # 运行时生成,不能由 LLM 自报
    numbers: list[NumberAnchor] = Field(default_factory=list)
    badges: list[str] = Field(default_factory=list)
    created_at: datetime
    content_digest: str
    rendered_from_payload_digest: str  # 检测 payload/rendered 漂移
class ArtifactRelation(BaseModel):
    event_id: str; relation: Literal["supersedes","refutes","approves","rejects"]
    from_artifact_id: str; to_artifact_id: str; created_at: datetime

# ── Event envelope / 冻结快照 ────────────────────────────────────────────
class RunEvent(BaseModel):
    schema_version: str = "1"
    event_id: str; run_id: str; partition: str; plan_digest: str | None = None
    event_type: Literal["RunRequested","PlanDrafted","PlanApproved","PlanRejected","PlanFrozen",
                        "BudgetReserved","BudgetSettled","BudgetReleased","NodeStateChanged","ArtifactStaged","LayerCommitted",
                        "ContextSnapshotFrozen","ArtifactRelated","ExperimentStateChanged",
                        "RunCancelled","RunCompleted","RunFailed","TrialReserved","TrialRevealed","TrialExhausted",
                        "CaseCreated","CaseMatured","CaseReviewed"]
    causation_id: str | None = None; correlation_id: str | None = None
    journal_seq: int                      # 内部 append 顺序;所有事件都有
    visible_seq: int | None = None        # 普通订阅游标;staged 永远为空
    idempotency_key: str
    payload_type: str; payload_version: str; payload_ref: str
    occurred_at: datetime; content_digest: str

class EventCursor(BaseModel):
    run_id: str; partition: str; visible_seq: int
class Subscription:
    cursor: EventCursor
    def next(self) -> RunEvent: ...

class CommittedArtifactRef(BaseModel):
    artifact_id: str; artifact_seq: int
class LayerCommit(BaseModel):
    plan_digest: str; layer_index: int
    node_run_ids: list[str]
    artifacts: list[CommittedArtifactRef] # 按 (node_id,output_key) 规范排序
    committed_at: datetime

class PlanApproval(BaseModel):
    request_id: str; plan_digest: str
    decision: Literal["approved","rejected"]
    actor_id: str; decided_at: datetime; reason: str | None = None

class ContextSnapshot(BaseModel):
    schema_version: str = "1"
    id: str; run_id: str; as_of: datetime; mode: DataMode
    bootstrap_plan_digest: str
    market_factor_ref: ArtifactRef
    regime_ref: ArtifactRef | None = None
    rotation_ref: ArtifactRef | None = None
    past_context_ref: ArtifactRef | None = None
    data_snapshot_id: str; data_snapshot_hash: str; vintage_manifest_digest: str
    memory_snapshot_id: str; memory_snapshot_hash: str; past_context_hash: str
    status: Literal["ready","degraded","unknown"]
    created_at: datetime; content_digest: str

class MemoryRecordRef(BaseModel):
    record_id: str; revision_id: str | None = None
    available_at: datetime; content_digest: str

class InputSnapshot(BaseModel):
    schema_version: str = "1"
    id: str; run_id: str; plan_digest: str; layer_index: int
    context_snapshot_id: str | None = None # bootstrap 层为空;main 层必填
    artifact_refs: list[ArtifactRef]
    data_result_ids: list[str]
    memory_record_refs: list[MemoryRecordRef]
    frozen_at: datetime; content_digest: str

# ── 结构化业务 payload ──────────────────────────────────────────────────
class ResearchPlan(BaseModel):
    recommendation: PortfolioRating; rationale: str; strategic_actions: str
class PortfolioDecision(BaseModel):
    rating: PortfolioRating; executive_summary: str; investment_thesis: str
    price_target: float | None = None; time_horizon: str | None = None
class SentimentReport(BaseModel):
    overall_band: SentimentBand; overall_score: float = Field(ge=0,le=10)
    confidence: Confidence; narrative: str
class MarketFactorValue(BaseModel):
    factor_id: str; definition_version: str; value: float | None
    params: dict[str,Any]; universe: str; frequency: str
    effective_at: datetime; available_at: datetime
    coverage: float = Field(ge=0,le=1)
    status: Literal["ok","unavailable"]
    missing_policy: str; content_digest: str
class MarketFactorReport(BaseModel):
    as_of: datetime; values: list[MarketFactorValue]
    data_snapshot_hash: str; coverage: float = Field(ge=0,le=1)
    content_digest: str
class RegimeReport(BaseModel):
    trend: Literal["bull","bear","range","unknown"]
    risk_state: Literal["risk_on","risk_off","neutral","unknown"]
    heat_state: Literal["normal","overheat","unknown"]
    trend_probabilities: dict[str,float]
    risk_probabilities: dict[str,float]
    heat_probabilities: dict[str,float]   # 每轴独立归一到 1;禁止混成一个无命名空间 dict
    confidence_score: float = Field(ge=0,le=1)
    drivers: list[str]; narrative: str
class RealizedRegime(BaseModel):
    horizon_trading_days: int = Field(gt=0)
    forward_return: float; max_drawdown: float; realized_volatility: float
    realized_trend: Literal["bull","bear","range"]
    available_at: datetime; data_snapshot_hash: str
class RotationReport(BaseModel):
    mainlines: list[str]
    stage: Literal["启动","扩散","分化","退潮","unknown"]
    strength: float = Field(ge=0,le=1)
    persistence_days: int | None = None
    narrative: str
class TargetPosition(BaseModel):
    symbol: Symbol; target_weight: float = Field(ge=0,le=1)
    stop_loss_pct: float | None = Field(default=None,gt=0,le=1)
    take_profit_pct: float | None = Field(default=None,gt=0)
    max_hold_bars: int | None = Field(default=None,gt=0)
class PortfolioTargetProposal(BaseModel): # LLM 只允许生成此 payload
    positions: list[TargetPosition]; cash_weight: float = Field(ge=0,le=1)
    rationale: str; confidence: Confidence
class TargetPortfolioIntent(BaseModel):  # runtime-only envelope
    intent_id: str; target_version: int = Field(gt=0)
    proposal_artifact_id: str; proposal_digest: str; source_decision_artifact_id: str
    decision_schedule_id: str; decision_schedule_version: str; decision_schedule_digest: str
    scheduled_for: datetime; decision_as_of: datetime; eligible_execution_at: datetime
    valid_until: datetime | None = None
    positions: list[TargetPosition]; cash_weight: float = Field(ge=0,le=1)
    origin: Literal["LLM"] = "LLM"
    authority: Literal["ADVISORY_ONLY"] = "ADVISORY_ONLY"
    execution_scope: Literal["SHADOW_ONLY"] = "SHADOW_ONLY"
    rationale: str; confidence: Confidence
    created_at: datetime

class DecisionSchedule(BaseModel):
    id: str; version: str; calendar_id: str; timezone: str
    kind: Literal["daily","weekly","rebalance_dates","manual"]
    decision_local_time: time; cutoff_local_time: time
    bar_frequency: Literal["1d","60m","30m","15m","5m","1m"]
    execution_policy: Literal["next_open","next_bar_close"]
    execution_price_field: Literal["open","close"]
    matching_engine_version: str
    weekdays: list[int] = Field(default_factory=list)
    rebalance_dates: list[date] = Field(default_factory=list)
    intrabar_exit_priority: Literal["worst_case","stop_first","take_profit_first"] = "worst_case"
    content_digest: str

class DebateMessage(BaseModel):
    debate_id: str; round: int; turn: int; role: str
    artifact_id: str; created_at: datetime
class StudySpec(BaseModel):
    objective: str; objective_digest: str
    label_definition: str; label_digest: str
    universe_digest: str; frequency: str; split_policy_digest: str
    parent_family_id: str | None = None; change_reason: str | None = None
class StudyFamily(BaseModel):
    family_id: str; identity_digest: str
    objective_digest: str; label_digest: str; universe_digest: str
    frequency: str; split_policy_digest: str
    parent_family_id: str | None = None; change_reason: str | None = None
    governor_attestation: str             # ledger 校验;调用方不能自造 family_id
class HoldoutWindow(BaseModel):
    holdout_window_id: str; family_identity_digest: str
    start_at: datetime; end_at: datetime; matured_at: datetime
    data_snapshot_id: str; vintage_manifest_digest: str
    prior_window_ids: list[str] = Field(default_factory=list)
    non_overlap_attestation: str
class TrialRecord(BaseModel):             # 仅 TrialLedger 生成;调用方不可自填 family/lease
    schema_version: str = "1"
    trial_id: str; family_id: str; candidate_hash: str; parent_trial_id: str | None = None
    data_snapshot_hash: str; split_spec_hash: str; code_prompt_model_hash: str
    metrics_revealed: list[str]
    stage: Literal["validation","sealed_holdout"]
    status: Literal["reserved","revealed","failed","timed_out","inconclusive"]
    validation_result_artifact_id: str | None = None # sealed holdout 必须为空
    result_digest: str | None = None
    holdout_window_id: str | None = None
    holdout_lease_id: str | None = None
    lease_state: Literal["none","reserved","consumed","exhausted"] = "none"
    revealed_at: datetime | None = None
    idempotency_key: str; reused_from_trial_id: str | None = None
    created_at: datetime

class OptimizeRunState(BaseModel):
    experiment_id: str; family_id: str; status: ExperimentStatus
    candidate_hash: str | None = None
    resume_after: datetime | None = None; wakeup_key: str | None = None
    updated_at: datetime

class OptimizeResult(BaseModel):
    state: OptimizeRunState
    best_candidate_artifact_id: str | None = None
    validation_trial_ids: list[str] = Field(default_factory=list)
    stop_reason: str | None = None

class HoldoutReceipt(BaseModel):          # 可进入公共事件;不含可解引用结果
    trial_id: str; family_id: str; holdout_window_id: str
    status: Literal["revealed","failed","timed_out","inconclusive"]
    result_digest: str | None = None; revealed_at: datetime | None = None

class HoldoutLease(BaseModel):
    lease_id: str; trial_id: str; candidate_hash: str; holdout_window_id: str
    issued_at: datetime; expires_at: datetime; nonce: str; signature: str

class SealedEvaluationRecord(BaseModel): # 仅 sealed store + final-report/human capability
    trial_id: str; result_artifact_id: str; result_digest: str
    metrics_payload: dict[str,Any]; curve_ref: str | None = None
    created_at: datetime

class SealedCapability(BaseModel):
    token_id: str; scope: Literal["final_report","human_review"]
    principal_id: str; expires_at: datetime; signature: str

class TrialLedger:
    def resolve_family(self, study: StudySpec) -> StudyFamily: ... # governor 派生/签名
    def reserve_validation(self, study, candidate_ref, split_ref) -> TrialRecord: ...
    def reserve_holdout(self, study, candidate_ref, window_ref) -> TrialRecord: ...
    def reveal_validation(self, trial_id, result_artifact_id, result_digest) -> TrialRecord: ...
    def exhaust_holdout(self, trial_id, *, status, result_digest=None) -> HoldoutReceipt: ...
    def effective_trial_stats(self, study: StudySpec) -> dict: ...

class SealedResultStore:
    def put(self, lease_id, record: SealedEvaluationRecord) -> HoldoutReceipt: ...
    def get(self, trial_id, *, capability: SealedCapability) -> SealedEvaluationRecord: ...

class SealedEvaluatorGateway:
    def evaluate_once(self, frozen_candidate_ref, *, holdout_reservation_id,
                      lease_token: HoldoutLease) -> HoldoutReceipt: ...

class ArtifactPool:                       # 仅 public/run namespace;拒绝 sealed result refs
    run_id: str
    def stage(self, art, *, output_key, idempotency_key, commit_lease) -> Artifact: ...
    def commit_layer(self, layer, *, coordinator_lease) -> RunEvent: ... # 从 Plan/终态/winning leases 推导集合
    def get_typed(self, slot, model, *, snapshot_id) -> BaseModel | None: ...
    def history(self, slot, *, snapshot_id) -> list[Artifact]: ...      # 仅 committed/public
    def freeze_input_snapshot(self, layer) -> InputSnapshot: ...
    def append_relation(self, relation: ArtifactRelation) -> None: ...
    def subscribe(self, kinds, cursor: EventCursor | None = None) -> Subscription: ...
    def snapshot(self) -> dict: ...
```

**运行不变量**:
- 所有 digest 使用版本化 canonical JSON(字段排序、UTC 时间、finite 浮点/精度规则);每类 schema 显式声明语义字段与审计字段。随机 id、墙钟、event seq 不进入语义 content digest,避免把调度噪声伪装成内容变化。
- `OrchestrationRequest.workflow=optimize_existing` 时 candidate id/hash/context snapshot 三者必填且相互校验;其他 workflow 禁止夹带它们。schedule id/version/digest 必须同有同无;任何产 shadow intent 的 request 必须绑定已注册 schedule。
- `DataResult` 的 `OK/DEGRADED` 必须有 typed data,其余状态不得夹带可消费数据;`DEGRADED` 强制 `coverage` 与 `degradation_reason`,多行 payload item 必须实现 `PitRecord`。语义 `content_digest` 覆盖 data、PIT 元数据、解析后 chain/config 与 attempt outcome 顺序,但排除 started/finished/fetched 等易变墙钟;完整审计字段另算 `audit_digest`。
- `RegimeReport` 三轴概率表各自只允许对应轴标签、值 finite 且和为 `1±1e-8`;`unknown` 必须由 coverage/evidence 不足触发,不能与高置信叙事矛盾。
- `TargetPortfolioIntent` 只能由 runtime 从已校验 `PortfolioTargetProposal` 包装。stage 前拒绝 duplicate symbol、NaN/Inf、负权重/做空/杠杆;v1 A 股多头要求 `abs(sum(target_weight)+cash_weight-1) <= 1e-8`,禁止后端静默归一化。时间满足 `decision_as_of < eligible_execution_at` 且若有 `valid_until`,则 `eligible_execution_at <= valid_until`。
- `DecisionSchedule` 的版本、时区、cutoff、日历、bar frequency、execution policy/price field、matching engine version 与同 bar 多退出条件优先级均进入 digest;runtime 用这些字段唯一计算 `scheduled_for/eligible_execution_at`。`next_open↔open`、`next_bar_close↔close` 必须配对。Broker 以 `(intent_id,scheduled_for,target_version)` 幂等应用 target;订单以 `(target_apply_key,symbol,order_kind,trigger_bar,ordinal)`,fill 以 `(order_id,fill_seq)` 去重,均保留 causation key。
- holdout `TrialRecord` 只能从 `reserved/reserved` 原子转为 `revealed/consumed` 或 `failed|timed_out|inconclusive / exhausted`;所有终态都关闭 `(family,window)` lease,进程恢复只能返回原 receipt,没有 reopen 转移。
- public partition 的 RunEvent/ArtifactRef/payload_ref 不能指向 sealed/review namespace;`TrialRevealed/TrialExhausted` 对 holdout 只允许 `HoldoutReceipt` payload。含 holdout 指标的 review 报告不可被普通 ArtifactPool、memory facade 或 ContextSnapshot 引用;namespace/capability 检查发生在写入与读取两端。
- NodeRun 是事件折叠出的只读视图。终态不可原地改;retry 新建 attempt。取消/超时与完成竞争时,只有仍持有 commit lease 的 attempt 可 stage,只有 `LayerCommitted` 可见。
- “审计回放”默认只重建已持久化的 Plan/NodeRun/DataResult/模型响应/Artifact,不调用模型或外部工具,应逐字重建。“重新执行”使用同一冻结输入新建 `replays_run_id`,外部 LLM 即使配置相同也不承诺逐字一致;只承诺相同已记录 worker 输出下归并与调度顺序无关,且已提交输出不重复。

**Plan freeze validator(硬门)**:
- `phase=bootstrap` 时 `context_snapshot_id` 必为空且只能运行版本化 Bootstrap catalog;`phase=main` 时必须引用已冻结 ContextSnapshot,从而消除自举循环。
- node id 唯一、worker 存在、DAG 无环、所有 sink 可达;`Dependency.artifact_slot/upstream_output_key` 与上游 `WorkerSpec.outputs` schema/kind 匹配。v1 `BLOCK.accept_statuses` 必须恰为 `{COMPLETED}`;`INCOMPLETE/FAILED/BLOCKED/CANCELLED` 永远不能声明为成功状态;`DEGRADE/SKIP` 的后继状态须显式。
- 同槽多写默认拒绝,除非 Plan 注册 catalog-owned 确定性 reducer;condition 只允许受限 DSL。`params` 必须通过对应 WorkerSpec input model;辩论节点的 `(debate_id,round,turn,role)` 唯一且与 `turn_order/max_rounds` 完全展开,预算按展开后 invocation 数校验。
- deterministic worker 必须有 catalog-owned handler;LLM worker 必须有 model tier;mode/工具/MCP/skill/evidence policy 相容。有效降级策略取 `Dependency` 与 `EvidencePolicy` 中更严格者,Planner 不能用软依赖绕开 worker 证据要求。
- 动态 Plan 只能缩小 WorkerSpec allowlist,不能加工具/服务器/路径;condition DSL 与 stop condition ID 必须过 registry;实际辩论轮次、retry 均计预算。
- 所有 Plan 均可有研究/上下文 sink;仅输出 decision-class payload 的 sink 必须 `can_emit_decision=True`。Gate metric/scope 必须来自版本化 registry;blocking gate 遇 `unavailable` 默认失败,只有显式 policy 可降级。
- `PlanDraft.approval_policy` 只能由 runtime 从 `OrchestrationRequest`/受信 preset 复制,Planner 不能选择 `AUTO`。`plan_digest` 对规范化 `PlanDraft` 的全部可执行字段、ContextSnapshot/catalog digest 和预算 request 计算,不含审批事件本身。freeze 时从唯一 RunBudget 原子预留并写 `budget_reservation_id`;bootstrap/planner/main/repair/retry 共用该总账。`approval_policy=required` 的执行器必须找到绑定**同一 plan_digest**的 `PlanApproved` 事件;审批事件是唯一事实源,禁止把 approved_by/at 双写回 Plan。修改任何字段都产生新 digest 并重新审批。

**旧 schema 迁移**:现有 rating(-10..10/五档)、action(大小写)、confidence(0..100/枚举)、sentiment(-1..1/0..10)、rotation stage 等差异经版本化、可逆 adapter 转换并保留 raw 值;禁止静默强转。迁移期旧 swarm YAML 与新 Plan 对同一静态图必须产生等价依赖状态。

---

## 9. 落子 & 帷幄 adapter(`adapters/`)

- **落子(replay)**:`DataMode.PIT_REPLAY` + 严格 raw PitReader adapter。以既有 `Broker/VirtualPortfolio/CostModel/limit helpers` 为确定性成交基线,复用 `BacktestRunner` 的 clock/结果汇总;实现无 LLM 的 `ShadowDecisionAgent` 或专用 `ShadowBacktestRunner` 把冻结 `TargetPortfolioIntent` 适配成引擎 Decision/Order,补齐 take-profit/max-hold/完整公司行动账本等缺口。按显式 `DecisionSchedule` 在各决策点跑 Bootstrap/必要 MainPlan,不是每 bar 全跑,也不是一次今天判断覆盖整段。区间完成后再把统一口径双曲线送 Evaluator。
- **前后端镜像迁移分两阶段**:①按浏览器现状定义 compatibility profile(零成本、单标的、同 bar 收盘成交、无停牌/涨跌停/T+1/手数/reject ledger),固定 bars/intents 比对 `trades/exit/curve/metrics`;②后端完整规则用独立 golden harness 验证 fill/reject/cost/公司行动。若要逐笔全等,须先扩展前端测试 harness;不得拿前端当前不具备的行为设伪门槛。通过兼容门后前端逐步只消费后端结果。
- **帷幄(live)**:`DataMode.ONLINE`,`as_of` 为 run 启动时冻结的带时区 timestamp,reader 经 live_client adapter;Bootstrap 形成 ContextSnapshot 后执行开放研究;`evaluate_validation=run_graph`,产物仅 draft 入 factorlib。
- 两者共用同一 Plan validator、`dag.run`、event log、ArtifactPool、memory facade、Evaluator-Optimizer;只换 DataContext/evaluator/shadow binding。旧 agent/YAML/schema 经版本化 adapter 接入,不静默改含义。

---

## 10. 预算 / 档位 / 执行边界(贯穿)

诚实脊柱(draft-only · typed payload · runtime provenance · 数字溯源+徽章 · evidence-policy incomplete 闸) · 预算/安全 · 执行边界贯穿所有 phase。

- `RunBudget.max_llm_invocations` 是**整个 run 总调用次数**,覆盖 Bootstrap、Planner、MainPlan、每一席/轮、schema repair 与 retry;默认≤24。Plan 只提交 `budget_request_*`,freeze 时从同一 ledger 原子取得 `budget_reservation_id`,不能各自再获 24 次。`max_concurrency` 是同时在跑的上限,二者不得混称“座席”;辩论≤2轮、仅 PM 使用 `reasoner_deep`。
- `RunBudget.max_tokens` 是 run 级硬预算:每次 Bootstrap/Planner/Plan/Node/schema repair/retry 都先取得 `BudgetReservation`,结束后按真实 usage settle,未使用额度显式 release;三种转换分别写 `BudgetReserved/BudgetSettled/BudgetReleased`,因此非 NodeRun 调用也可完整重建。并发任务不能先跑后统一发现超支。provider/model/vendor 各有独立 semaphore/rate limiter。
- timeout/cancel 产生明确 NodeRun 终态;超时不能假装已杀死仍在后台写盘的工具。正式 stage/commit 均需检查 attempt 仍持有 commit lease。
- Planner 只能缩小 catalog allowlist;worker 不得 spawn 子 agent、扩大 tools/MCP、写 real signal/order、直接写 memory/skill/code。工具/上游文本按不可信数据隔离,provenance 以真实事件为准。
- `ADVISORY_ONLY/SHADOW_ONLY` 在 capability 层硬隔离:live adapter 不注册订单/信号写工具;任何非 shadow target 直接拒绝。影子只由确定性 clock/Broker 执行,不动真钱。

---

## 11. 测试与验收

- **Plan/状态单元**:bootstrap 无 ContextSnapshot 可执行、main 缺 snapshot 必拒;node 唯一/unknown worker/环/sink 不可达/schema 不匹配/同槽多写/非法 input-model params/辩论 round-turn/越权工具均拒绝;研究 sink 合法、decision sink 必须有权限;硬依赖失败→blocked,软依赖 unavailable→degraded,但更严格 EvidencePolicy 可在派发前 blocked/派发后 incomplete;gate unavailable 严格按 policy。合法 no-tool worker completed,要求工具却零调用→incomplete。request 级 fallback 在 Planner 失败前仍可用;审批必须绑定同一 digest;`optimize_existing` 三个冻结引用缺一即拒。
- **数据/PIT 单元**:`normalize_symbol` bare/dotted/engine code 与路径安全;ST/limit rule 由 metadata+as_of 决定且 unknown 显形;dispatch 只对 RateLimit/NotConfigured 跨 vendor;NoData/Stale 终止;FutureDataRefused 永不回落。财报按 available_at/vintage,不是 fiscal period。
- **PIT 不变量**:①存储查询正确下推 `available_at<=as_of` 时,新增未来 data/memory/case 不改变旧日期 hash;②adapter/raw result 若把未来 row/vintage/memory 交给 strict PitGuard 则 `FutureDataRefused`;③任一行缺 `available_at` 必拒;④cache backend 不改变 strict 语义且完整保留 PitAudit/badge;⑤strict replay 缺不可变 snapshot/vintage manifest 必拒,新增 revision 不改变旧 manifest 结果;⑥ DataContext/ClockSpec 的 as_of/calendar 不一致必拒。
- **并发/恢复性质测试**:随机打乱线程完成顺序、重复投递、retry、迟到 stage、进程中断;未过 barrier 的 staged 输出不进 visible stream,Coordinator 缺终态/必需 output/有效 winning lease 时不能 commit,commit 后关闭层并拒迟到写。snapshot/artifact_seq 按 Plan 规范顺序一致,journal_seq 与 visible_seq 游标各自可恢复,同层不可互读。已持久化模型响应的审计回放逐字一致;未提交 LLM attempt 重跑不要求文本 hash 相同,但只能有一个正式提交。
- **LLM/诚实/安全**:typed payload schema repair 有界;runtime tool/provenance 与 LLM 自报不一致时以 runtime 为准;数字 anchor value/path/source 对得上;上游 prompt injection 不得扩大权限。
- **预算账本**:Bootstrap/Planner/Plan/Node/repair/retry 的 reserve→settle/release 事件可完整重建,并发预留永不超 RunBudget;崩溃恢复不重复授予额度。
- **Governor/Optimizer**:L0 必须早于昂贵 evaluate;停滞签名命中不重复求值;调用方伪造/改名 family attestation 必拒,参数候选仍累计到同 family/TrialLedger;同一 holdout window 仅一个候选且 reservation 先写,失败/超时/inconclusive 均耗尽;新窗口只能来自后来成熟且不重叠的 OOT 数据。公共 Pool/event 无法解引用 sealed 指标,只有 final-report/human capability 可读;holdout 指标不可到达 improve/L3/memory;`WAITING_FOR_MATURITY` 幂等唤醒;purge/embargo/nested walk-forward 边界;统计样本不足返回 unavailable。
- **口径一致**:市场因子经 `run_graph` 与画布/帷幄逐位一致;旧 SubAgent/YAML 与新静态 Plan 的波次、软硬依赖和阻断结果等价。
- **影子镜像/e2e**:先用前端实际支持的 compatibility profile(零成本、单标的、同 bar 收盘成交、无停牌/涨跌停/T+1/手数/reject ledger)比对 trades/exit/curve/metrics;后端丰富成交规则独立做 golden tests。intent 必引用真实 Proposal Artifact 与 schedule digest;bar frequency/policy 唯一算出 eligible time。重复恢复只应用一次 target,但不同 order kind/trigger bar 与多 fill 不被误吞;滚动曲线确在每个 schedule 点使用当时 PIT 快照。落子出同口径双曲线;帷幄出 draft;经验库 append-only 成熟链;Lane D 轮次与预算守卫真拦。
- **红线回归**:LLM 无 real order/signal capability;任何 shadow intent 不能原地提升实盘;动态 Plan 未审不执行;无显式 fallback preset 不回落;draft 不自动上架;worker 不直改 memory/skill/code。

---

## 12. 分期实施建议(供 writing-plans 细化)

1. **契约冻结与迁移表**:`OrchestrationRequest/WorkerSpec/PlanDraft+Plan/PlanNode/Dependency/RunBudget+Reservation/NodeRun/RunEvent/Artifact/DataResult/Context+InputSnapshot` + schema registry/version;建立现有 agent/YAML/rating/action/confidence/stage → 新契约的可逆映射。
2. **静态 runtime 兼容**:把现有 `financial_analyst.agent.orchestrator.Orchestrator/DAGNode + financial_analyst.agent.base.SubAgent/SubAgentResult + financial_analyst.swarm.loader.load_preset` 接入 strict validator、staged→barrier ArtifactPool、双游标 RunEvent 与预算账本;先跑 3-worker 静态 Plan,再对 `stock-deep-dive` 做旧/新依赖语义等价测试。此阶段不启动态 Planner。
3. **数据/PIT + memory facade**:`data/*` typed result/窄回落/PitGuard/Symbol;统一现有 memory **读取 facade 与 proposal 提交入口**,批准后的写入继续调用既有 `memory_ops`/console writer,不引入双写;补 data/memory snapshot hash。具体 vendor 获取仍可占位。
4. **Evaluator-Optimizer / Governor**:从 `research/loop` 抽状态机、从既有统计实现抽纯函数;先让现有因子研究 adapter 回归,加入 governor-attested family、TrialLedger、一次性 holdout lease 与独立 sealed result store。
5. **Bootstrap Lane 0 + 经验库**:固定 BootstrapPlan、市场因子 coverage、PIT-safe RegimeCase、按时间历史种子和延迟 grader;先 unknown/degraded 诚实运行。
6. **先钉住 shadow 消费端**:实现最小 `PortfolioTargetProposal → runtime TargetPortfolioIntent → ShadowDecisionAgent/ShadowBacktestRunner → Broker` 与 compatibility mirror,冻结组合/时间/幂等不变量;避免 Lane D 先迁到未经成交端验证的 schema。
7. **动态 Orchestrator + Plan 人审**:基于 ContextSnapshot 生成 MainPlanDraft;strict validate、request 级显式 preset fallback、digest-bound approval event;稳定前动态 Plan 默认 required。
8. **四车道目录/skills/Lane D**:分批迁移现有 report/swarm worker;有界辩论用多实例 PlanNode+不可变消息;预算/模型档位与旧入口隔离,`dec.trader` 只产 Proposal。
9. **完整落子/帷幄 adapters**:补后端 clock/公司行动差异、前后端分阶段镜像、DecisionSchedule 与双曲线;红线/并发/恢复/e2e 全绿后才逐步下线旧入口。

---

## 13. 未决 / 挂账

- **数据源具体获取实现**(决定 3 已定接口,vendor 抓取暂缓;akshare/tushare 取舍待定——astock 因积分墙弃 tushare、v0.2.18 声称零 akshare 依赖,本仓 `datafeed/live_client` 已用二者,以本仓为准)。
- **市场因子清单增删**(是否加涨停连板情绪、行业拥挤度、期权/融资情绪)。
- **typed 迁移批次**:原则和可逆 adapter 已定;现有 swarm/report/bull-bear-risk-writer 的逐批次名单、兼容期与旧入口删除门槛待 writing-plan 明确。
- **各研究族 split 口径**:train/validation/rolling sealed holdout 的具体时间窗、purge/embargo 长度和新封存窗口策略。
- **经验库标签阈值**:趋势/风险/热度三轴 schema 已定;自动派生阈值、LLM 打标比例与人审抽样率待历史 coverage 实测。
- **Lane 0 数据覆盖**:NH-NL、历史炸板率、北向/主力长分位、HHI/离散度、估值历史分位哪些可做严格 replay;未达 coverage 的保持 UNAVAILABLE。
- **影子镜像容差**:后端确定性成交基线已定;前端旧 `runBacktest` 的 compatibility profile 对 trades/exit/curve/metric 的允许误差、是否扩展完整 fill/reject/cost harness 及切换日期待实现计划定义。
- **Plan 人审承载面**:动态 v1 默认 required 已定;不新建页面前,具体由现有控制台/报告/API 哪一处展示 plan diff 与 approval event 待实现计划选择。
