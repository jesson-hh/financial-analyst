# 编排框架接入帷幄/落子 · 接入收口设计(spec)

日期:2026-07-18 · 状态:**用户已逐节确认**(brainstorming 六项拍板见 §9)
基线:Phase 1-3 已建成(contracts/静态runtime/data-PIT+memory,1950+ tests);Phase 4 执行中(task 6 已落);Phase 5-9 计划已 R2 reconcile(`eb313e6`);R2 agent 目录 spec = `2026-07-16-orchestration-round2-agents.md`(27 席)。
本 spec 不重写 Phase 5-9 计划——它们已是接入主体;本 spec 只定义**两处外科修订 + 一个新 Phase 10 计划**。

---

## 0. 定位:两条用户用例(本设计的纲)

| 用例 | 系统 | 一句话 | 架构承接 |
|---|---|---|---|
| **选股** | 帷幄 | 用更多信息源、按安排好的调研逻辑推荐股票 | Lane 0 情境 → 候选节点族 → 逐票四车道深研 → Lane D 裁决 → advisory 推荐榜 |
| **买卖点** | 落子 | 选好票后专注个股走势,深度推理给买卖点 | pv.* + text.news + 情境快照 → 辩论 → dec.pm(deep) → dec.trader 目标仓位带 + 分批触发价区间 |

结论(用户确认):现有 Phase 1-9 体系**结构上能满足**这两条,R2 spec 8.3 的 `dec.trader` 输出(仓位带+触发价区间)就是"买卖点"的契约形态。三个诚实边界:①一切产出 advisory,LLM 零买卖,人执行;②回放求值引擎 v1 仅日线,盘中级买卖点的*回放验证*需 Phase 6 撮合引擎升版(deferred),第一期验证以日线为准;③K线经典形态词典(AMEND-6a,P0)未落地前 `pv.price_action` 形态字段诚实显空。

Phase 5-9 已覆盖的接入面(不重复设计,仅引用):P7 console 审批卡(`/plan/approvals` 三端点 + JSX 卡);P8 能力清单从 `WW_TOOL_TABLE` 机器生成;P9 `/orchestration` 路由挂 9999、luozi 区间回放按现有 shape 写 `var/seats_runs.jsonl`+`var/seats_decisions.jsonl`(`source:'orchestrated'`,RunPicker 零前端改动)、weiwo 因子研究适配器(factorlib draft 落地)、watcher 预算/跳票两接缝、autonomy 成熟唤醒、三条 legacy 入口退役闸。

## 1. 交付物总览(三件)

1. **修订一(进 P7 计划)**:ApprovalLease 预批凭证。
2. **修订二(进 P9 计划)**:jsonl 持久化后端 + 9999 重启 resume + 每日 Lane 0 三门调度。
3. **Phase 10「接入收口」新计划**(锚定 P9 出口闸,Task 0 handoff 模式同 P4):A 帷幄选股流水线 ∥ B 落子买卖点 live 换脑(两条并行)+ C 公共件。

修订施行方式:R2 同款——本 spec 定稿后出 reconcile checklist,对 P7/P9 计划外科手术修订,不重写。

## 2. 修订一(P7):ApprovalLease 预批凭证

动机:内核红线"每 Plan 必人审(REQUIRED),AUTO 全拒"与盘中自动深链/每日 Lane 0/回放 N 决策点冲突。用户裁定:**预批凭证**——不是放宽 AUTO,是人签的、有界的、可撤销的批量授权。P7 计划本就预留"对 preset 放宽 AUTO 属未来受审变更",本修订将其写实。

- **契约 `ApprovalLease@1`**(注册进 registry,自封 content_digest):绑定已鉴定 preset 族(目录 digest + preset digest,任一漂移即失效)+ 三重上限:有效期(UTC 窗)、次数上限、预算上限(kernel BudgetLedger 单位)。
- **签发**:console 审批卡加"签发租约"动作 → 新端点入 P7 已规划的 `/plan/approvals` 族 → 写同一本 `var/orchestration/plan_approvals.jsonl`(append-only+fsync,PlanApprovalCoordinator 崩溃重放,P7 原设计复用)。
- **准入**:PlanAdmissionService 加受控通道:`source==PRESET` ∧ preset 已鉴定 ∧ digest ∈ 租约族 ∧ 期/次/预算三闸全过 → 自动准入 + 持久化 LeaseAdmission witness(租约 id + 消耗序号);任一闸不过 → 回落逐个人审,绝不静默放行。
- **红线**:DYNAMIC 来源 Plan 永不吃租约;AUTO 仍全拒;撤销 = 日志追加 `lease_revoked` 一行即刻生效。
- **先例**:Phase 4 HoldoutLease(digest 绑定 + 一次性额度 + 越权即拒)同模式推广。

## 3. 修订二(P9):持久化 + resume + 每日 Lane 0

- **文件后端**(纯加法,Phase 2 存储接口不动,内存实现继续服务测试):事件流按分区落 `var/orchestration/events/*.jsonl`(append-only+fsync,repo 习语),payload 按 digest 落 `var/orchestration/payloads/`。这是 P9 自身出口闸的前提:成熟唤醒需 parked run 状态跨重启存活。
- **重启 resume 钩子**:9999 lifespan 启动扫描,未终态 run 依 Phase 2 已建成的 NodeRun@1 持久记录恢复;恢复不了的诚实标 `interrupted`(console bg 同款纪律)。
- **每日 Lane 0 调度**:三门模式原样复制(`GUANLAN_LANE0_DAILY=1` / `note=='daily-scheduler'` / 当日未跑)挂现有调度链,盘前跑 bootstrap → 当日 ContextSnapshot 备好 + RegimeCase 开始积累成熟。Lane 0 preset 静态已鉴定,吃长期租约(如每日 1 次)。

## 4. Phase 10-A:帷幄选股流水线

**候选节点族**(三个确定性 worker,按用户当次需求由规划器/preset 选用——用户裁定候选池不写死):

| 节点 | 行为 | 依赖 |
|---|---|---|
| `cand.v4` | 读现有 v4 榜 top-N(N 参数化),零 LLM | 现有选股产物,绝不动信号 |
| `cand.lane0` | 读当日 RotationReport 主线 → 题材内梯队/龙头,确定性抽取 | §3 每日 Lane 0 已跑 |
| `cand.model` | 读经现有 `ww_model_*` 通道训练的模型变体的榜单 | 训练走既有 draft 门,流水线只读产物 |

**Plan 形态**:一个 Plan 装 N 个并列逐票子图(`text.news` + `text.research_report` + `quant.factor/backtest` + `pv.price_action` → `dec.bull`/`dec.bear` 一轮辩论 → `dec.research_mgr` 五档评级)+ 一个全场汇总节点(`dec.pm` 横向比较产推荐榜)。**一个 Plan = 一次审批看全貌**(审批卡显示 N 票 × 预计 LLM 调用 × 预算)。

**触发两路**:聊天 `ww_orchestrate_start`(goal → 规划器 → 审批卡);固定配方(如"每日 v4 top10 深研")做成已鉴定 preset 后签租约日跑。

**产出三落点**:①推荐榜工件(逐票评级+汇总)→ console 卡 + SSE;②研报型 md → 现有 `POST /archive/put`(type=research)进 GL 证据库——**落子研判从此可引用选股深研结论(两系统证据库汇流)**;③选股页 overlay 徽章后置(红线:绝不动 v4 信号)。

## 5. Phase 10-B:落子买卖点 live 换脑

用户裁定:**分级升级为主 + 策略实例 opt-in 叠加**。

- **换脑点**:`watcher.tick` 的 `decide_fn` 注入参数(watcher.py:358,现成)。包 `orchestrated_decide`:默认原样走现快链(`_decide_impl` 单 LLM);命中升级才走深链。
- **升级判定器 = 纯函数零 LLM**(全分支单测):①快判方向翻转;②价格触及止损/止盈带 ±x%(阈值参数在 Phase 10 计划冻结,不留浮动);③个股重大事件命中(确定性词表,烈度分层:立案>问询>关注函);④形态词典关键形态命中(AMEND-6a 落地后启用)。策略实例 `strat_*.json` 加 `deep_research: true` 则该票盘中研判恒走深链(该文件系前端写非契约 JSON,后端 best-effort 读,诚实处理)。
- **深链 = 已鉴定静态 preset `luozi-deep-decide-v1`**:`pv.price_action` + `pv.technical` + `pv.microstructure` + `text.news`(个股)+ 复用当日 ContextSnapshot(不重跑 Lane 0)→ 一轮辩论 → `dec.pm`(deep 档,A股六条约束前置)→ `dec.trader` 目标仓位带 + 分批触发价区间(= 买卖点)。
- **准入**:盘中租约(如 4 次/日、今日有效);超额自动回快链。**深链失败或超额永远诚实降级回快链结果并打标,绝不断供盘中。**
- **落地**:决策记录按现有 shape 写 `seats_decisions.jsonl`(`source:'orchestrated'`+run_id,现有页零改动可见);触发价区间落现有条件单记录(advisory,人执行);预算走 P9 `reconcile_daily_llm_budget` 单一规则 + 租约预算上限。
- **上盘中前先过堂**:同 preset 先跑 Phase 9 回放双曲线(深链 vs 快链 vs 纯规则,同区间同撮合配置 digest)——有证据表明深链真改善买卖点,才签盘中租约。不盲上。

## 6. Phase 10-C:公共件

- **三只 ww 工具**(五处同步纪律:工具表/`_SYSTEM_PROMPT`/守护计数/MCP 自动派生/接口文档生成器;MCP 暴露随表免费得到):`ww_orchestrate_start`(confirm=True,收 goal 或 preset_id,返回 request_id+"等待审批"诚实状态,工具不代批)/ `ww_orchestrate_status` / `ww_orchestrate_runs`。
- **D7 投递入口收编**(P8→P9 之间的孤儿):`ww_ta_ingest` 工具 + 固定收件目录 `var/ta_inbox/`(必须标注来源作者,FSI 不可信隔离);先落收件箱,#27 pv.curator 真跑后消费。
- **preset 族注册**:选股配方族 + `luozi-deep-decide-v1` 进 Phase 10 累积目录(P9 之上加链,旧目录快照照常可回放)。
- **预算账本定位**:编排 run 唯一记账 = kernel BudgetLedger;seats 24/日池经 P9 调和;autonomy(12 次/job)与 BuddyAgent(token/turn)两本账不动(挂账,不在本期强行统一)。

## 7. 错误处理与测试

- **错误纪律**:深链失败 → 降级快链 + 决策记录打 `degraded` 标(绝不冒充深链成功);租约超额 → 拒 + console 提醒事件;运行中重启 → resume 或诚实 `interrupted`;候选节点缺数据(如 Lane 0 当日未跑)→ 显式拒绝并说明,不静默换源。
- **测试**:全 TDD;租约三闸逐一单测 + LeaseAdmission witness golden;升级判定器纯函数全分支;`source:'orchestrated'` 记录与现有 reader 兼容绊线;五处同步守护计数更新;回放双曲线 = 深链 preset 验收闸。

## 8. 红线清单(全部继承,无一新破)

LLM 零买卖(kind=trade 永远人手);推荐/买卖点全 advisory;v4 信号不动(overlay only);DYNAMIC Plan 逐个人审;AUTO 全拒;sealed holdout 一次性;PIT 整批拒绝;UI 只填充不重建;协程内禁同步自 HTTP;绝不 `git add -A`。

## 9. 拍板记录(2026-07-18 用户逐项裁定)

| # | 决策 | 裁定 |
|---|---|---|
| I1 | 设计范围 | 按两用例重梳理:帷幄=选股(多源调研推荐)、落子=个股买卖点(深度推理);确认现体系结构上能满足 |
| I2 | 选股候选池 | 按用户当次需求定:指定 v4 走 v4 / 要重新判断走 Lane 0 / agent 可先训模型再读榜——候选做成节点族,不写死 |
| I3 | 盘中深链触发 | 分级升级(推荐)+ 策略实例 opt-in,两者叠加 |
| I4 | 审批张力 | 预批凭证 ApprovalLease(人签、digest 绑定、期/次/预算三限、可撤销);DYNAMIC 仍逐个人审 |
| I5 | 两条流水线顺序 | 并行 |
| I6 | 打包方式 | 混合:ApprovalLease 修进 P7、持久化/resume/每日 Lane 0 修进 P9(R2 同款外科修订);两条流水线+公共件立独立 Phase 10 计划 |

## 10. 依赖与顺序

- 修订一/二随 P7/P9 计划本体执行(P4 尚在执行,P5-9 未动工,修订零返工成本)。
- Phase 10 计划现在写,Task 0 以可执行消费者测试锚定 P9 出口闸(P4 Task 0 模式);A/B 两条在 Phase 10 内并行,C 公共件先行(ww 工具依赖 P9 `/orchestration` 路由)。
- 外部前置(已有 owner,非本 spec 范围):快照归档小 phase(补历史回放地板)、AMEND-6a 形态词典种子、val.pct 估值源(stocks 层)。
