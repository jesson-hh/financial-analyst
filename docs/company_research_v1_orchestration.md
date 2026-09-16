# `company_research_v1` 正式编排契约（安全切片）

审计日期：2026-09-04

## 结论

当前能够完整执行公司研究闭环的是 **Console 的 Codex 专用入口 + company
typed-v2 冻结计划**。公开 `/gl-mcp` 的 detached report 仍不是公司闭环入口。
company-v2 使用独立的严格 catalog/schema/materializer，不伪装成通用 preset，
也没有改动既有 preset/golden。

本切片新增：

- 版本化配方 revision 2：`config/research/recipes/company_research_v1.json`
- 严格 loader 和只读 launch adapter：
  `guanlan_v2/research/company_recipe.py`
- typed catalog/schema/materializer/runtime：
  `guanlan_v2/research/company_orchestration_v2.py`
- durable report owner：`guanlan_v2/research/company_report_process.py`
- 定向测试：`tests/test_company_recipe.py`、
  `tests/test_company_orchestration_v2.py`、
  `tests/test_codex_company_research_console.py`

adapter 仍只生成 `awaiting_confirmation` 或 `unavailable` 契约，不审批、不调度、
不写工作空间。真实批准由 Console 的单次 `confirm_id` 产生；批准、预算预留和
frozen Plan 一同写入 durable receipt。

## 现有真实接缝

| 顺序 | 逻辑阶段 | 当前真实实现 | 约束 |
|---|---|---|---|
| 1 | `prepare` | `ww_workspace_company_research_prepare` / `workspace_company_research_prepare_impl` | 从当前 `CTX_WORKSPACE` 解析唯一 stock workspace 和 A 股 subject；建立私有准备包。该独立工具当前为 `confirm=False`，但只写当前私有空间。 |
| 2 | `research_report` | durable `company_report_process` + Console `_run_report_bg` | 只在 frozen Plan 与确认/预算 receipt 全部复核后启动；同 job 可重连 helper。 |
| 3 | `risk_gate` | typed `company.risk-gate`，复用生产 `evaluate_company_research_report_gates` | 模型不能自报 issuer provenance；引用重新对 Evidence Hub 解析。 |
| 4 | `report_gate` | typed `company.report-gate`，复用 `validate_company_research_report(..., strict=True)` 的结果 | 上游状态、证据、计算和风险契约共同决定最终状态。 |
| 5 | `workspace_asset_finalizer` | typed `company.finalize` → `finalize_company_research_report` | Gate 预检哈希与最终资产必须一致；同准备包+报告哈希只允许唯一资产。 |

五个阶段均有严格输入/输出 schema 和物料校验 worker。计划固定为串行 DAG、
`max_attempts=1`；没有隐式重试或动态 callable/path 覆盖。生产 Gate 仍复用同一份
底层计算，finalizer 会重算并核对哈希，避免预检与落盘逻辑漂移。

## 服务端绑定与审批边界

`CompanyResearchLaunchRequest` 只有 `as_of`；模型或客户端传入
`workspace_id`、`code`、`subject` 会因 `extra="forbid"` 被拒绝。adapter 的
binding resolver 是无参数函数，必须由服务端当前会话上下文产生：

```text
current Console session
  -> active stock workspace
  -> workspace-owned SH/SZ/BJ subject
  -> trusted binding digest
  -> read-only launch contract (pending confirmation)
```

launch contract 同时绑定：

- 配方原始文件 SHA-256 和语义 SHA-256；
- 当前 session/workspace/subject SHA-256；
- 公开请求 SHA-256；
- 完整契约 SHA-256。

未来真正 dispatch 之前必须调用 `revalidate_binding()`。配方字节变化、契约被改、
会话换空间、空间换标的或空间失效都会 fail closed。adapter 没有 `approve()` 或
`dispatch()` 方法，不能被当成绕过 Console 确认门的 token。

## 通路状态（配方 revision 2）

| 入口 | 状态 | 证据/缺口 |
|---|---|---|
| Console | **connected** | 服务端 subject 绑定、单次确认、frozen typed-v2 Plan、durable report、严格 Gate 与 finalizer 已接线。 |
| `/gl-mcp` | **unavailable** | 现有 detached report 是通用报告启动器；缺 trusted workspace/subject binding、公司准备包和严格 workspace finalizer。 |
| `/gl-mcp-codex` | **implemented / live smoke pending** | 后续安全接缝已用 Codex-only facade 连到 Console background/finalizer；不改变本 revision 对公开 `/gl-mcp` 的 unavailable 判定。详见 `docs/company_research_v1_codex_mcp.md`。 |
| company orchestration v2 typed DAG | **connected（Console 内）** | 6 个 schema、5 个物料校验 worker、专用 materializer、确认/预算/Plan 哈希复核、Gate 与 finalizer binding 已接线；不等于开放通用 v2 preset。 |

配方中的 unavailable 是硬状态，不会回落到通用报告后再宣称完成
`company_research_v1`。

## 重启语义

公司研究的 Console 协调任务本身是进程内的，但准备包以 job-id 幂等，报告由
durable helper 持有并落原子结果 receipt。服务重启后，启动扫描会重新核验
workspace/code/root/subject/binding、frozen Plan、catalog/schema、确认记录和预算预留：

- `resume_across_restart=true`
- `stale_running=resume_same_job`
- `relaunch=explicit_new_request`

`queued/preparing/running` 在合同复核通过后按原 job 续跑；有存活 helper 时重连，
没有可信结果且 helper 已消失时按同一 job 补跑一次。任何绑定或合同漂移都
fail closed。`finalizing` 绝不重放 finalizer，只能采纳按准备包与报告哈希唯一匹配
且通过资产校验的已有原生资产。

## 后续可选工作

1. **是否接公开 `/gl-mcp`**：若未来开放，仍需只能由服务端铸造的当前 Console scope/binding，复用公司
   prepare pack 解析和 finalizer；MCP 参数中仍不能出现可任选的 workspace id。
2. **生产现场 smoke**：服务重载后，在安全测试个股空间由用户亲自批准一次，核验
   queued receipt、durable report、Gate 哈希和最终 `wa_*` 资产。
3. **真实用量结算**：当前 frozen reservation 明确上限并由 900 秒报告超时兜底；
   legacy 引擎尚未回传逐 agent 精确 token/LLM 用量，因此 receipt 不伪报 actual usage。

## 验证

```powershell
python -m pytest -q tests/test_company_recipe.py tests/test_company_orchestration_v2.py `
  tests/test_codex_company_research_console.py
```

覆盖：固定阶段/依赖、禁止自动审批、客户端 workspace/subject 注入、无效 binding、
配方/合同篡改、typed schema/binding/gate、确认绕过拒绝、排队后取消、同 job 重启
续跑、finalizing 唯一资产采纳以及重复 JSON key 拒绝。
