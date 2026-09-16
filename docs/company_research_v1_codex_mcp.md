# `company_research_v1` 的 `/gl-mcp-codex` 安全接缝

审计与实现日期：2026-09-04

## 当前结论

`company_research_v1` 已有一条**代码内可执行、typed-v2 全合同接线且定向测试
通过**的 Codex 专用接缝。它不是公开 `/gl-mcp` 的通用报告工具，也没有开放
`ww_report_run`。在生产界面标成全绿前，仍需重载实际 9999 服务并完成一次由
用户点击确认的受控 smoke；在这之前，准确状态是“实现与回归已通过，现场验证待做”。

```text
当前 Codex turn 的短期 scope
  -> ww_workspace_company_research_run(asof?)
  -> Console 每次生成一个新 confirm_id，等待用户 y/n
  -> 按当前 sid 重新绑定唯一 stock workspace / A 股 subject / root
  -> 生成并冻结 5 节点 typed-v2 Plan
  -> 把确认、预算、Plan/catalog/schema 哈希写入 queued receipt
  -> 再返回 MCP 受理回执
  -> Console background lane（MCP 请求线程不等待长研报）
       -> workspace_company_research_prepare_impl
       -> resolve_company_report_pack
       -> durable report helper 持有真实 legacy report 子进程
       -> company.risk-gate（生产 Gate 计算）
       -> company.report-gate（strict report Gate）
       -> company.finalize（Gate 哈希复核 + exactly-once 资产）
       -> 原生 wa_* workspace asset
```

## 暴露面

| 通道 | `ww_workspace_company_research_run` | 通用 `ww_report_run` |
|---|---:|---:|
| 公开 `/gl-mcp` | 不存在 | 保持原有公开写门策略；不作为公司闭环 |
| 受限 `/gl-mcp-codex` | 仅在有效 scope、确认回调和 launch 回调齐备时可见 | 明确关闭 |
| Console 内部后台 | 通过窄 callback 调度 | 真实 legacy lane 仍由 Console 自己使用 |

专用工具的公开参数只有可选 `asof`。`workspace_id`、`code`、`subject`、
`prepared_asset_id`、报告路径和后台 `job_id` 均不能由模型或 MCP 客户端提供。

## 逐次确认与绑定

确认发生在执行点，不是 Codex 的通用自动审批：

1. `/gl-mcp-codex` 在显示确认前核验 scope、workspace、subject 和 root；
2. Console 为本次调用生成新的 `confirm_id`；
3. 拒绝、确认超时、轮次取消或 scope 撤销均不调用 launch callback；
4. 用户批准后再次核验同一绑定；
5. launch callback 再从当前 sid 读取 workspace，不信任确认弹窗或客户端参数；
6. durable `queued` receipt 成功落盘后才创建后台任务。

私有 launch callback 还会检查与本次 `confirm_id` 绑定的单次批准证明；自定义
Agent 即使直接调用 callback，也不能绕过确认。批准证明用后即清除。

受理回执只表示 `queued`，不能当成报告完成。最终完成必须同时存在 Console 的
终态 `task_update`、`tool_result` 和原生 `wa_*` 资产。

## 后台与重启语义

后台 receipt 的状态为：

```text
queued -> preparing -> running -> finalizing -> done | error
```

- `preparing` 调用现有公司准备包，但关闭全局 events 写入和 NewsRadar 主动刷新；
- `running` 使用 durable helper 执行真实 legacy 报告生成，而不是在 MCP 调用线程中等待；
- 进入 `finalizing` 前先持久化 `prepared_asset_id + report_sha256`；
- finalizer 返回后写 `done` 和原生资产引用。

服务重启按严格合同做同 job 续跑：

- 遗留 `queued/preparing/running` 重新核验 workspace/code/root/subject/binding、
  frozen Plan、catalog/schema、批准与预算；全部一致才按同一 job 续跑；
- 报告 helper 仍存活时只重连；已有原子结果 receipt 时直接采用；
- 任何合同或绑定漂移均标 `resume_refused/error`，不启动报告或 finalizer；
- 遗留 `finalizing` 只在找到相同准备包与相同报告哈希的唯一原生资产时，采纳
  已完成结果；
- 找不到、关键字段不全或出现多个匹配资产时均 fail closed；
- 启动扫描绝不重放 finalizer。

用户在确认前取消或确认超时不会创建后台 receipt。确认并成功排队后，可调用
`POST /console/company-research/cancel` 请求合作式取消：`queued/preparing/running`
会在下一个安全阶段边界停止，且不会启动后续 finalizer；`finalizing` 已越过可取消
边界，只能等待唯一终态。

## 尚未接通或仍需决定

1. **生产现场 smoke**：需重启实际服务，在一个测试 stock workspace 中由用户确认
   一次，核验 Codex inventory、queued receipt、真实报告和最终 `wa_*`。
2. **精确用量结算**：Plan 已冻结 160,000 token / 25 次 LLM 调用上限并设置
   900 秒报告超时；legacy report 引擎目前不回传逐 agent 精确 actual usage，故
   receipt 只记录 reservation，不伪造已结算用量。
3. **公开 `/gl-mcp`**：仍不提供公司闭环入口；这是有意隔离，不是遗漏。

## 定向验证

```powershell
python -m pytest -q `
  tests/test_codex_company_research_mcp.py `
  tests/test_codex_company_research_console.py `
  tests/test_codex_backend.py `
  tests/test_guanlan_mcp.py `
  tests/test_console_api.py
```

覆盖专用工具不可从公开 MCP 发现、闭合参数、逐次批准/拒绝、callback 绕过拒绝、
确认后绑定漂移、受理回执、typed Plan/catalog/schema/预算/批准哈希、prepare →
durable report → strict Gate → finalizer、排队后取消、同 job 重启续跑、唯一资产
采纳，以及既有 Codex/MCP/Console 回归。
