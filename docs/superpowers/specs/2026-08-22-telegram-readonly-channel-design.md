# 观澜 Telegram 只读会话通道设计

**日期：** 2026-08-22
**状态：** 自动化假端到端、无密钥本地 UI 与安全审查已完成；真实 Telegram Bot 尚未验收
**范围：** 帷幄对话只读镜像、严格一对一 Telegram Bot 绑定、显式媒体投递、会话内定时任务
**参考：** OpenClaw 的显式配对与通道隔离、NanoBot 的消息总线与会话绑定自动化

## 1. 目标

为观澜增加一个可扩展的外部消息通道层。首期接入 Telegram，但不把 Telegram 变成第二个控制台：帷幄仍是唯一真实会话、唯一 Agent 输入入口和唯一权限控制面；Telegram 只是一个会话级、只读的消息投影。

用户在电脑端帷幄发送的可见文字和 Agent 的最终回复，按顺序同步到当前对话唯一绑定的 Telegram Bot 私聊。思考过程、工具调用、运行进度和内部错误不发送。图片、图表和报告只有在用户明确要求发送，或经用户确认的定时任务明确要求附带时，才通过安全媒体仓投递。

设计必须兼容未来飞书、Discord 等通道。Telegram 特有逻辑不得侵入 Agent、研究空间、落子或帷幄会话核心。

## 2. 已确认的产品决策

1. Telegram 首期只出站，不接受 Telegram 对 Agent 的对话、命令、打断或转向。
2. 一个帷幄对话最多绑定一个 Bot；一个 Bot 同时也只能绑定一个帷幄对话。
3. Bot 显示名称由用户命名；配对成功后，对话标题显示 `已连接 Telegram · <Bot名称>`。
4. 绑定在配对成功后永久有效，跨服务重启保留，直到用户解绑、Token 失效或 Bot 身份改变。
5. 配对请求使用一小时有效的临时挑战码；临时码过期不影响已完成的永久绑定。
6. Telegram 只同步配对成功后的新消息，不补发此前历史。
7. 定时任务属于具体帷幄对话；结果先进入帷幄历史，再投影到 Telegram。
8. Bot Token 由 Agent 引导用户提交，但不能进入模型 Prompt、普通会话事件、日志或 Outbox。
9. Telegram 故障不得阻塞帷幄消息落盘或 Agent 运行。
10. 发送结果不确定时避免盲目重试；不得伪称 Telegram Bot API 能提供 exactly-once 投递。

## 3. 非目标

首期不实现以下能力：

- 从 Telegram 向 Agent 发送问题或追加方向；
- 从 Telegram 调用 Skill、MCP、研究空间、文件系统、落子、审批或其他工具；
- Telegram 群组、频道、话题、多用户或一个 Bot 绑定多个会话；
- Telegram 内联按钮控制 Agent；
- 自动把所有生成图片、附件或报告外发；
- 为 Telegram 建立独立会话历史、记忆、Prompt 或模型配置；
- 直接嵌入 OpenClaw/NanoBot，或运行第二套 Agent 核心；
- 通过 Telegram Bot API 之外的自定义 API 根地址发送 Token；
- 承诺网络分区下的严格 exactly-once。

## 4. 不可破坏的系统边界

### 4.1 帷幄是唯一事实源

Telegram 中出现的普通文字必须来自已经写入 `ConsoleStore` 的可见会话事件。定时晨报和研究结果也必须先成为帷幄历史的一部分。Telegram 不得持有比帷幄更多的会话语义。

### 4.2 Agent 与通道解耦

Agent 只能调用窄工具发起连接、查询状态、批准配对、管理定时任务和显式发布媒体。Agent 不直接调用 Telegram HTTP API，不接触 Token，不决定任意接收者，也不能改变一对一绑定。

### 4.3 Telegram 入站永不成为 Agent 输入

入站更新仅在未配对阶段用于确认真实 Telegram 用户和私聊 Chat。配对完成后，该 Bot 的所有普通入站文字、媒体、按钮和命令在通道边界终止，不写入帷幄，不加入 Buddy/OpenAI 消息链，不触发工具。

### 4.4 现有功能保持原行为

未连接 Telegram 的对话必须与当前行为一致。落子、研究空间、SSE、会话归档、任务打断/转向、图片媒体仓和 Agent 工具白名单不因通道层改变语义。所有 Telegram 网络 I/O 都在帷幄消息主路径之外执行。

## 5. 总体架构

```text
ConsoleStore / 帷幄可见事件
              │
              ▼
     ConsoleMessageProjector
       （持久游标、白名单）
              │
              ▼
        Durable Outbox
              │
              ▼
        Channel Router
              │
              ▼
       Telegram Adapter
              │
              ▼
        Telegram Bot API
```

旁路组件：

- `ChannelRegistry`：Bot、绑定、状态和一对一约束；
- `SecretStore`：Bot Token 的系统级加密存储；
- `TelegramPairingService`：入站轮询、配对请求和批准；
- `ConversationAutomationService`：会话级定时任务与运行记录；
- `ChannelManager UI`：对话标题状态、对话内安全卡、通道管理抽屉；
- `Channel Agent Tools`：Agent 可调用的窄控制面。

## 6. 组件职责

### 6.1 ChannelRegistry

负责保存 Bot 账户元数据、会话绑定和状态。它是唯一可以创建或解除绑定的领域服务，必须在存储层建立以下唯一约束：

- `binding.session_id` 唯一；
- `binding.bot_account_id` 唯一；
- 活跃 Bot 的 Telegram `bot_id` 唯一；
- 一个绑定只允许一个 Telegram `user_id + chat_id`。

前端禁用按钮不能代替这些约束。并发配对、重复提交或服务重试都必须由后端原子拒绝冲突。

### 6.2 SecretStore

暴露 `put/get/delete` 的窄接口。Windows 首期实现使用当前服务用户作用域的系统加密能力保护 Token；磁盘只保存密文和不透明 `credential_id`。更换 Windows 服务账户后无法解密时，连接进入 `credential_unavailable`，要求用户重新提交，而不是降级为明文。

任何 Agent 工具、API DTO、日志或异常都不得返回 Token。通道适配器只在实际发送线程中短暂解密，使用后释放引用。

### 6.3 TelegramPairingService

为状态为 `awaiting_pairing/active/paused` 且凭证健康的 Bot 持续推进 Telegram update offset，但只在存在待配对草稿时解释入站更新。`poll_conflict`、凭证故障和清理中状态必须停止 poller，等待显式恢复，不能持续争抢。未知私聊用户产生 8 位大写配对码，字符集排除 `0/O/1/I`；请求一小时过期；每个 Bot 最多保留三个待处理请求。Bot 已激活或暂停后的普通入站更新只推进 offset 并立即丢弃，不创建事件、不缓存正文，也不在未来重新配对时回放为陈旧请求；断开后停止 poller。

配对批准必须发生在电脑端的当前帷幄会话，并满足：

- 配对码、Bot、待绑定会话和绑定草稿一致；
- Bot 和会话均没有其他活跃绑定；
- 请求来自私聊；
- Telegram Bot ID 与 Token 经 `getMe` 验证的身份一致；
- 用户明确确认永久绑定。

批准只授予“接收该会话出站投影”的资格，不授予 Telegram 入站对话权、命令所有者权或工具审批权。

### 6.4 ConsoleMessageProjector

使用持久游标读取已经落盘的帷幄事件，并按事件白名单生成 Outbox 项。它不能依赖一次性的进程内回调，否则“事件已写入、Outbox 尚未写入”时崩溃会永久漏发。

投影键至少包含 `binding_id + console_event_id + payload_part`，在 Outbox 存储层唯一。重启、重扫或游标回退只能命中既有项，不能生成重复记录。

### 6.5 Durable Outbox

负责持久状态、每绑定顺序、失败分类和恢复。网络 worker 与帷幄主事件循环解耦。Outbox 不保存 Token、本地路径、研究空间根目录或完整内部工具结果。

### 6.6 Channel Router

输入是标准化 `OutboundEnvelope`，至少包含通道、绑定、来源会话、来源事件、内容类型、可见文本或媒体 ID、序号和审计元数据。Router 只根据已持久化绑定选择适配器；Agent 不提供任意 `chat_id` 或接收者。

### 6.7 Telegram Adapter

首期仅实现：

- 发送安全纯文本；
- 发送媒体仓中的 PNG/JPEG/WebP；
- 获取 `getMe` 身份；
- 配对阶段接收私聊更新。

适配器不得拥有 ConsoleStore 写权限、Agent 引用、研究空间工具或任意文件读取能力。

### 6.8 ConversationAutomationService

保存会话级定时任务。到点后向指定会话提交一次受控系统轮次；若会话忙，则排队等待空闲，不打断当前轮次。Agent 最终输出照常写入帷幄，再由 MessageProjector 投影。

任务不得直接调用 Telegram Adapter，也不得在帷幄之外生成“隐藏晨报”。

## 7. 数据模型

### 7.1 BotAccount

建议字段：

- `id`：观澜内部不透明 ID；
- `display_name`：用户命名；
- `telegram_bot_id`：`getMe` 返回的稳定数字 ID；
- `telegram_username`：仅展示；
- `credential_id`：SecretStore 引用；
- `status`：`draft/awaiting_pairing/active/paused/poll_conflict/credential_invalid/credential_unavailable/credential_cleanup_pending/disconnected`；
- `created_at/updated_at/last_verified_at`。

### 7.2 ChannelBinding

建议字段：

- `id`；
- `channel = telegram`；
- `session_id`；
- `bot_account_id`；
- `telegram_user_id`；
- `telegram_chat_id`；
- `status`：`active/paused/disconnected`，故障原因另存且不与主动暂停混用；
- `paired_at/sync_from/paused_at/disconnected_at`；
- `last_sent_at/last_error_code`。

`sync_from` 是历史边界。MessageProjector 不得投影早于该时间或绑定起始事件 ID 的内容。

### 7.3 PairingRequest

建议字段：

- `id`；
- `bot_account_id`；
- `session_id`；
- `telegram_user_id/chat_id/username/display_name`；
- `code_digest`，不持久化明文配对码；
- `created_at/expires_at/last_seen_at`；
- `status = pending/approved/dismissed/expired`。

### 7.4 OutboxItem

建议字段：

- `id`；
- `binding_id`；
- `source_session_id/source_event_id`；
- `sequence`；
- `kind = text/media`；
- `text` 或 `media_id`；
- `state = pending/sending/retry_wait/sent/uncertain/terminal/cancelled`；
- `attempts/next_attempt_at`；
- `telegram_message_id`；
- `last_error_class/last_error_at`；
- `created_at/sent_at`。

### 7.5 ConversationAutomation

建议字段：

- `id/name/session_id`；
- `schedule_kind = once/daily/weekdays/trading_days`；
- `run_at/local_time/timezone`；
- `prompt_text/skill_refs`；
- `media_policy = none/explicit`；
- `enabled`；
- `next_run_at/last_run_at/last_status`；
- `created_by/confirmed_at`。

首期默认时区为 `Asia/Hong_Kong`，但每次创建或修改必须向用户复述完整时间与时区。
首期不接受任意 cron 或 event 表达式；`trading_days` 必须使用真实 A 股交易日历，日历不可用时诚实阻塞，不能以周一至周五近似。

## 8. Agent 主导的连接流程

1. 用户在当前对话说“连接 Telegram”，可同时提供 Bot 显示名称。
2. Agent 按系统 Prompt 调用 `ww_telegram_connect` 创建绑定草稿。
3. 对话渲染安全 Token 输入卡。Token 直接提交 SecretStore，不经过 Agent。
4. 后端调用 Telegram `getMe`，仅把 Bot ID、用户名、基础显示信息和校验状态返回 Agent。
5. Agent 提示用户私聊该 Bot。未知用户的普通私聊只创建配对请求，不进入 Agent。
6. Bot 向用户显示一小时有效的配对码；电脑端也显示待请求的安全元数据。
7. 用户把配对码告诉 Agent。
8. Agent 调用 `ww_telegram_pair_approve`。工具必须展示永久绑定确认卡。
9. 用户确认后，ChannelRegistry 原子建立严格一对一绑定。
10. 对话标题显示 `已连接 Telegram · <Bot名称>`，同步从批准后的下一个可见事件开始。

如果用户把看似 Telegram Bot Token 的字符串误贴到普通输入框或运行中的转向输入框：

- `/console/send` 与 `/console/steer` 必须在写 `user_msg`、转向控制或取消请求前共用同一服务端检测；
- 当前有连接草稿且带有绑定当前会话与草稿的一次性请求令牌时，将 Token 直接安全提交并只落占位事件 `[Telegram Bot Token 已安全提交]`；
- 没有有效一次性请求令牌时安全拒绝并引导重新提交，不能把秘密落盘；
- 没有连接草稿时拒绝普通发送并弹出安全连接卡；
- `/console/steer` 命中 Token 时不得打断当前 Agent；
- 原始 Token 不得出现在 HTTP 错误、浏览器日志、服务日志或响应回显中。

客户端检测只是体验增强，服务端落盘前拦截才是安全边界。

## 9. 消息投影规则

### 9.1 自动镜像

仅投影以下语义事件：

- 用户在电脑端成功写入的普通可见消息；
- Agent 一轮完整结束后的最终可见回复；

定时任务必须先作为受控系统轮次进入帷幄，其最终回复同样以 `agent_final` 投影；首期不增加宽松的通用通知事件白名单。

### 9.2 永不自动镜像

- reasoning/thinking；
- 流式 delta；
- tool_call/tool_result；
- 计划内部状态；
- 后台任务进度；
- 确认卡内容和密钥输入；
- 原始异常、堆栈或内部诊断；
- 配对入站消息；
- 研究空间文件、路径、资产或图片，除非显式发布。

### 9.3 文本拆分

Telegram 文本使用安全纯文本。超过单条限制时按段落优先拆分，再按安全字符边界拆分。所有分片共享来源事件和批次 ID，并按 `payload_part` 唯一排序。首期不使用模型生成 MarkdownV2 转义，也不因格式化失败改变原文事实。

### 9.4 显式媒体

用户明确说“把这张图/这份结果发到 Telegram”后，Agent 调用 `ww_telegram_publish`。工具只接受当前会话/研究空间已发布到 Console MediaStore 的不透明 `media_id`，不得接受绝对路径或任意 URL。

发送确认卡必须显示缩略图、说明和唯一目标 Bot。确认后 Outbox 保存 `media_id`，适配器在发送时通过 MediaStore 读取已验证字节。普通图片生成不会自动触发此流程。

## 10. 定时任务与 Prompt/Skill

### 10.1 创建与管理

用户可以在对话中说：

- “每天 08:15 在这个对话生成盘前晨报”；
- “每个交易日收盘后复盘，并同步 Telegram”；
- “暂停晨报”；
- “把晨报改到香港时间 08:30”。

Agent 使用带确认门的自动化工具创建、修改、暂停、恢复或删除当前会话任务。缺任务目标、执行时间、时区或输出要求时，Agent 逐项询问，不自行猜测。

### 10.2 Prompt 与 Skill 分工

- Prompt 定义本任务目标、关注范围和输出要求；
- Skill 定义稳定流程、数据优先级、引用要求、失败降级和输出结构；
- Schedule 只定义何时、在哪个会话、使用哪些 Prompt/Skill 运行；
- Channel Binding 只定义结果投影到哪个唯一 Telegram 目标。

Bot Token、Chat ID 和其他通道凭证不得进入 Prompt 或 Skill。

### 10.3 运行语义

定时任务到点后创建一次系统来源的会话轮次。内部触发指令不镜像；Agent 最终可见结果正常镜像。任务要求附图时，仍必须在已确认的任务配置中设置 `media_policy=explicit`，并只发送该轮显式发布到媒体仓的资产。

会话忙时，任务等待该会话空闲。服务重启后，错过的任务按“每任务最多补跑一次”的策略进入待运行状态，不能无限追补历史周期。归档会话自动暂停其通道绑定和定时任务；恢复会话后保持暂停，等待用户显式恢复。

## 11. Agent Prompt 与工具边界

帷幄系统 Prompt 增加以下稳定纪律：

1. 用户说“连接 Telegram”时，调用安全连接工具，不要求用户在普通消息中粘贴 Token。
2. 缺 Bot 名称、Token 或配对码时逐项索取；Token 必须通过安全卡提交。
3. Agent 永远不能读取、复述、导出或发送 Token。
4. 用户提供配对码后，调用批准工具并展示永久绑定确认。
5. 用户要求“把图/报告发到 Telegram”时，只能发送 MediaStore 中的媒体。
6. 用户要求定时发送时，必须复述时间、时区、任务内容和图片策略，并走确认门。
7. Agent 不得声称 Telegram 可以反向控制、已实现 exactly-once、已发送未获成功回执的消息，或未确认的定时任务已经生效。

建议的窄工具面：

- `ww_telegram_connect`：创建当前会话绑定草稿并返回安全卡；
- `ww_telegram_status`：读取当前会话连接与投递摘要；
- `ww_telegram_pair_status`：读取当前 Bot 待配对请求；
- `ww_telegram_pair_approve`：确认并建立永久一对一绑定；
- `ww_telegram_pause/resume/disconnect`：管理当前绑定；
- `ww_telegram_publish`：显式发布受控媒体；
- `ww_automation_list/upsert/pause/resume/delete`：管理当前会话定时任务。

所有写工具必须限定当前会话，不能接受任意 `session_id`、Bot ID 或 Chat ID 作为模型参数。

## 12. 界面与状态

### 12.1 对话标题

- 未连接：不显示标记；
- `Telegram · 待配置`；
- `Telegram · 等待配对`；
- `Telegram · 配对请求待确认`；
- `已连接 Telegram · <Bot名称>`；
- `Telegram · 重试中`；
- `Telegram · 投递待确认`；
- `Telegram · 需要重新连接`；
- `Telegram · 已暂停`。

### 12.2 对话内卡片

- 安全 Token 输入卡；
- Bot 身份校验卡；
- 配对请求确认卡；
- 连接成功卡；
- 定时任务确认卡；
- 媒体发送确认卡；
- 投递结果不确定处理卡。

### 12.3 通道管理抽屉

展示当前绑定、Bot 与 Telegram 用户的非秘密元数据、最近成功发送时间、队列数量和最近投递记录。提供暂停、恢复、重新校验、替换 Token 和解除连接。永远不提供“查看 Token”。

解除连接需要确认。解绑立即取消尚未发送的 Outbox 项，停止新投影，但不删除帷幄会话历史；重新绑定从新 `sync_from` 开始，不补发旧消息。

## 13. 安全设计

### 13.1 秘密隔离

- Token 只存在于安全输入请求、SecretStore 和适配器实际请求内存中；
- 所有 DTO、日志与审计信息使用 `credential_id` 和 Bot ID；
- 异常清洗必须在记录前移除 Telegram Token 形状；
- 测试快照和 fixture 不使用真实 Token；
- 替换 Token 后 Bot ID 变化必须重新配对。

### 13.2 请求边界

沿用帷幄本机同源限制。安全 Token 提交、配对批准、解绑和自动化写操作还需不可重放的短期请求令牌。浏览器跨源请求、`Origin: null` 和不可信 `Sec-Fetch-Site` 必须拒绝。

### 13.3 Telegram 边界

- API 根固定为 Telegram 官方 Bot API；
- 首期仅私聊；
- 只接受与待配对 Bot 对应的更新；
- 绑定完成后仍推进 update offset，但不把入站载荷传给任何业务模块，也不持久化正文；
- 不启用 webhook 暴露本机端口，首期使用单实例长轮询；
- 同一个 Token 只能由一个观澜 poller 使用，冲突必须显形。

Telegram Bot API 把 Token 放在 HTTPS 请求路径中，因此 HTTP 客户端调试日志和异常 URL 必须关闭或在记录前按 `bot<token>` 结构脱敏；不能只清洗 JSON 请求体。

### 13.4 审计

记录：创建草稿、Token 校验结果、配对请求、批准/驳回、暂停/恢复、解绑、Token 替换、定时任务变更、媒体显式发布和投递状态变化。审计记录不含 Token、配对码明文、消息全文或本地路径。

## 14. 可靠性与恢复

### 14.1 状态机

Outbox 状态为：

```text
pending → sending → sent
             ├─→ retry_wait → sending
             ├─→ uncertain
             └─→ terminal
pending/retry_wait → cancelled（解绑）
```

每个绑定同一时刻只处理一项，保证顺序。

### 14.2 重试分类

- 建连前明确失败：退避重试；
- Telegram 明确限流：按 `retry_after` 等待；
- 明确鉴权失败：标记凭证失效，停止自动重试；
- 用户屏蔽 Bot、Bot 不存在或目标不可达：标记终态并提示处理；
- 请求可能已到达但响应丢失：进入 `uncertain`，不盲目重发。

Telegram Bot API 不提供应用自定义幂等键；成功只返回 Telegram Message。因此 `uncertain` 会阻塞该绑定后续顺序，界面要求用户选择“视为已送达并继续”或“重新发送并接受可能重复”。系统不得自动替用户作出这个选择。

### 14.3 崩溃恢复

- MessageProjector 游标和 Outbox 同步持久化；
- `sending` 项在重启后不能直接当作未发送，必须转为 `uncertain`；
- `pending/retry_wait` 恢复调度；
- 成功项保存 Telegram `message_id`；
- 服务启动时验证活跃 Bot 身份，但验证失败不删除绑定。

### 14.4 熔断与可观测性

连续故障触发每绑定熔断，不影响其他 Bot。通道状态暴露活跃绑定、待发、重试、结果不确定、终态失败、最后成功时间和最后错误分类，但不暴露秘密。

## 15. 测试与验收

### 15.1 单元测试

- Bot/会话双向唯一约束；
- 配对码字符集、摘要、过期和每 Bot 上限；
- Token 落盘前拦截与日志脱敏；
- 事件白名单/黑名单；
- 长文本稳定拆分；
- Outbox 幂等键与顺序；
- 重试分类、熔断和 `uncertain`；
- 归档/解绑取消语义；
- 自动化时间、时区和错过任务补跑规则。

### 15.2 集成测试

使用假的 Telegram HTTP 服务验证：

- `getMe` 成功、401、Bot 身份改变；
- 配对长轮询和未知用户不进入 Agent；
- 发送文字、图片、限流、网络失败和超时；
- 服务重启后的游标、Outbox 和调度恢复；
- 同一 Token 双 poller 冲突显形；
- 没有 Telegram 时现有 `/console/send`、SSE、打断/转向和归档测试保持通过。

### 15.3 端到端验收

1. 用户只通过 Agent 引导安全提交 Token 并完成配对；
2. 对话标题显示正确 Bot 名称；
3. 电脑端用户消息和 Agent 最终回复按顺序到达 Telegram；
4. 思考、工具调用、进度和内部错误没有外发；
5. Telegram 普通消息无法影响 Agent；
6. 用户明确命令后可发送 PNG/JPEG/WebP；
7. 晨报到点后先出现在帷幄，再到 Telegram；
8. 断网、重启、限流、Token 失效和不确定结果均按状态机显形；
9. Token 不存在于会话、Prompt、日志、Outbox、API 响应和测试快照；
10. 未绑定 Telegram 的所有现有功能行为不变。

## 16. 兼容与演进

首期标准信封、绑定、Outbox、自动化和 SecretStore 都使用通用 `channel` 抽象。未来飞书接入只新增 Feishu Adapter、配对/授权实现和展示文案，不复制帷幄事件投影、Outbox、定时任务或 Agent 主流程。

双向 Telegram 对话如果未来立项，必须是独立规格：重新设计入站鉴权、会话路由、Prompt 注入边界、命令权限和工具授权，不能通过放开当前配对处理器直接获得。

## 17. 实施分期建议

1. 通用数据模型、SecretStore、唯一约束和状态 API；
2. Agent 安全卡、Token 校验和 OpenClaw 式收紧配对；
3. 持久 MessageProjector、Outbox、Telegram 文字投递；
4. 显式媒体投递；
5. 会话定时任务、Prompt/Skill 绑定；
6. 通道管理抽屉、健康状态、故障恢复与实机验收；
7. 在首期稳定后，以同一抽象增加飞书适配器。

每一阶段必须可独立测试、默认关闭，并保证未启用 Telegram 时零行为变化。
