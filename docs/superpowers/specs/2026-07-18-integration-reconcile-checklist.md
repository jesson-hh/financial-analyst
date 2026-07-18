# 接入收口 · P7/P9 reconcile checklist(已施行)

日期:2026-07-18 · 依据:`2026-07-18-orchestration-integration-design.md`(用户逐节确认,裁决 I1-I6)
方式:R2 同款外科修订(`2026-07-18-r2-reconcile-checklist.md` 先例)——只对**未执行**的 Phase 7/9 计划做加法修订,零回改已建代码,零重写计划结构。Phase 5/6/8 验证零影响(修订不触及)。

## A. Phase 7 计划(`2026-07-16-orchestration-phase7-dynamic-planner.md`)— ApprovalLease

| # | 位置 | 修订 | 状态 |
|---|---|---|---|
| A1 | Goal 段 | "three missing pieces"→four;新增第(4)件:`ApprovalLease` 有界常备审批 | ✅ |
| A2 | Global Constraints·AUTO 条 | 删"对 preset 放宽属未来受审变更/out of scope"句,改为:Task 7b 租约通道即该受审变更——不碰 `validate_plan_draft`,候选仍 REQUIRED,凭真实 `PlanApproval` 准入;DYNAMIC 永不匹配租约 | ✅ |
| A3 | File Structure | `approval.py` 职责行加租约通道;测试清单插 `test_approval_lease.py` | ✅ |
| A4 | **新 Task 7b**(Task 7 与 8 之间) | `ApprovalLease@1` 注册契约(digest 绑定 preset/catalog/registry + 期/次/预算三限)、journal row_kind 加 `lease_issued/consumed/revoked`、coordinator 四方法(`issue_lease`/`list_leases`/`revoke_lease`/`register_and_try_lease`)、`LeaseAdmissionOutcome`、六条不变量、TDD 矩阵、独立提交块。关键机制:租约在 Task 7 `decide` 内部以 `actor_id="lease:<id>"` 铸真实 `PLAN_APPROVED`——**换签名人,不换准入序列**;不匹配→`pending_human` 回落人审 | ✅ |
| A5 | Task 8 console | 三个租约端点(list/issue/revoke,同 503/403/409 纪律)、`plan_lease_issued/admitted/revoked` 会话事件、UI 卡«租约»节、测试矩阵补租约行 | ✅ |
| A6 | Task 9 registry | `PHASE7_PUBLIC_MODELS` 7→8(+`ApprovalLease`);internal 补 lease row kinds + `LeaseAdmissionOutcome`;矩阵 "7 new publics"→8 | ✅ |
| A7 | Task 10 e2e | 新场景 4「Lease lifecycle」(签发→自动准入→额度递减→耗尽/过期/撤销回落人审→崩溃重放对账);Red lines 递号 4→5 并补两条(DYNAMIC 永不匹配、陈旧 digest 拒漂移) | ✅ |
| A8 | Exit Gates | 新节「Standing approvals (lease)」四闸 | ✅ |
| A9 | Execution Handoff | 检查点 5:Tasks 6–7 → 6–7b,补租约包络/回落矩阵 | ✅ |

**绑定注记(A4 内)**:Task 6 的 `PendingPlanApproval` 自始定义 preset 溯源字段(`preset_id`/`preset_record_digest`,DYNAMIC 为 None)——计划未执行,不算回改。

## B. Phase 9 计划(`2026-07-16-orchestration-phase9-adapters.md`)— 耐久化 + resume + 每日 Lane 0

| # | 位置 | 修订 | 状态 |
|---|---|---|---|
| B1 | Goal 段 | 新增 (h) 耐久 jsonl 后端 + lifespan 诚实 resume/interrupt(Task 1b);(i) 每日 Lane 0 门经租约准入(Task 6) | ✅ |
| B2 | Global Constraints | 新增「Durability honesty」条:生产绑耐久存储/测试默认内存/重启只标 `interrupted` 绝不假装在跑/maturity heads 字节存活/journal 纪律(append-only+fsync+torn-tail 容忍+中段损坏硬失败) | ✅ |
| B3 | Task 0 第 4 项 | Phase 7 handoff 断言扩至租约四方法面(lane0 playbook 消费) | ✅ |
| B4 | Task 0 修正条款 | 新增 **C7**:租约 API 名称 Phase 7 所有,Tasks 1b/6/10 绑受审名 | ✅ |
| B5 | File Structure | 新行 `adapters/durable.py` + `test_durable_stores.py`;runtime/playbooks/rescore/server 四行更新(lane0 门、耐久绑定) | ✅ |
| B6 | **新 Task 1b**(Task 1 与 2 之间) | `FilePayloadStore`(内容寻址一次写+读时验 digest+namespace 物理分区)/`JsonlEventStore`(双游标字节等价)/`JsonlStateCellStore`(CAS fold)/`build_durable_runtime_stores`/lifespan 启动扫描(只标 interrupted、parked head 不碰);**一致性纪律 = 复用 Phase 2 同一套行为矩阵参数化跑耐久实现**;五条不变量、TDD 矩阵、独立提交块 | ✅ |
| B7 | Task 6 | 标题 +「daily Lane 0 gate」;新增 `maybe_enqueue_lane0_bootstrap` 三门 + `PLAYBOOKS["lane0_bootstrap"]`(preset→验证→保留→`register_and_try_lease`→租约准入→BOOTSTRAP profile 跑在耐久存储→快照+case seeds;无租约=诚实 skip 非错误);不变量 6;矩阵补三行 | ✅ |
| B8 | Task 10 | 路由生产接线绑 `build_durable_runtime_stores`(`GUANLAN_ORCH_STORE_ROOT` 可覆写),测试保持 tmp/内存 | ✅ |
| B9 | Exit Gates | 新节「Durable stores, resume and daily Lane 0」四闸 | ✅ |
| B10 | Execution Handoff | 检查点 2 补 Task 1b;检查点 4 补 lane0 门 | ✅ |

## C. 一致性核对

- 单一准入路径不破:租约存在于 Phase 1 验证 + Phase 2 保留**之后**,经 `record_approval` 铸真实审批,无第二条 freeze/admission 路;AUTO 仍全拒(A2/A8 双钉)。
- 无新 EventType:租约复用 `PLAN_APPROVED`(P7 约束原文兼容);P9 仍零新增。
- 范围保护不破:P7 修订只触 `approval.py`(Task 7 自有模块)+ console 加法;P9 修订只触 adapters/ 新文件 + 既有四处加法接缝文件;Phase 1–6/8 计划与代码零触碰。
- 命名审慎:所有新符号名受 Task 0 correction-clause 纪律约束(实现期对齐受审 API)。

## D. 后续(不在本 checklist)

- Phase 10「接入收口」实现计划(A 选股流水线 ∥ B 落子买卖点 + C 公共件)→ writing-plans,Task 0 锚定 P9 出口闸。
