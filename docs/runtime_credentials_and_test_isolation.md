# 运行凭据与测试输出隔离

本文是当前部署规则。较早设计稿中若出现“把任意环境变量写进
`var/secrets.env`”的示例，以本文和 `guanlan_v2/runtime_secrets.py` 为准。

## `var/secrets.env` 只存凭据

`server.py` 把 gitignored 的 `var/secrets.env` 当作本机守护进程的可选凭据
兜底，不把它当作通用 dotenv。只有下面这些精确名称允许进入环境：

- LLM：`ANTHROPIC_API_KEY`、`DASHSCOPE_API_KEY`、`DEEPSEEK_API_KEY`、
  `KIMI_API_KEY`、`OPENAI_API_KEY`
- 数据源：`FA_TUSHARE_TOKEN`、`HUGGINGFACE_TOKEN`、`TDX_API_KEY`、
  `TUSHARE_TOKEN`
- MCP 传输认证：`GUANLAN_MCP_BEARER_TOKEN`
- 严格 PIT：`GL_PIT_HMAC_KEY_ID`、`GL_PIT_HMAC_KEY`、
  `GL_PIT_HMAC_VERIFY_KEYS`

白名单是精确匹配，不能用 `*_API_KEY` 或 `*_TOKEN` 后缀绕过。未知名称、
重复项、空凭据和语法错误都会以稳定的 `blocked_entries` 状态显形；对应值
不会进入环境。已有非空进程凭据优先，文件不会覆盖。

`GUANLAN_EVENTS_INGEST`、`GUANLAN_MCP_WRITE`、
`GUANLAN_SERVER_BACKGROUND`、`GUANLAN_REGEN_DAILY`、Telegram 开关、路径、
超时、预算和所有其他 feature/runtime/write gate 必须由进程或服务管理器的
部署环境显式设置，绝不能从该文件开启。`GUANLAN_CODEX_SCOPE_TOKEN` 是一次
调用范围内的临时令牌，也不允许持久化在这里。严格 PIT 的 `GL_PIT_NTP_*`
字段是运维对 OS 校时结果的短期声明，同样必须来自部署环境。

## 9999 的非秘密运行配置

`scripts/check_9999.ps1` 现在持有一个封闭、可校验的非秘密运行配置。每一代
watchdog 会先完整验证，再一次性写入子进程环境；任意缺项、多项、非法值或依赖
冲突都会以 `[runtime-config-blocker]` 和退出码 `78` 停止，不能带着部分配置继续。
布尔项只接受字面量 `0`/`1`，`CONSOLE_REVIEW_MODE` 只接受
`off`/`monitor`/`enforce`。

当前复核后的姿态为：

| 配置 | 值 | 作用 |
|---|---:|---|
| `CONSOLE_REVIEW_MODE` | `enforce` | 帷幄复盘建议按既有强制模式落草稿 |
| `GUANLAN_DL_DAILY` | `1` | regen 链前置运行 DL 生产器 |
| `GUANLAN_EVENTS_INGEST` | `1` | 事件库定时入库 |
| `GUANLAN_MCP_WRITE` | `1` | 继承 9 月 5 日已复核部署配置；Codex 仍受独立 scope 与逐次确认约束 |
| `GUANLAN_ORCH_LAUNCHER` | `1` | 编排 launcher 绑定 |
| `GUANLAN_REGEN_DAILY` | `1` | 收盘后数据再生调度 |
| `GUANLAN_RERANK_DAILY` | `1` | regen 成功后重排 |
| `GUANLAN_REVIEW_DAILY` | `1` | 重排落定后日复盘 |
| `GUANLAN_SEATS_DEEP` | `1` | 席位 watcher 可升级深研判 |
| `GUANLAN_SEATS_WATCH` | `1` | 交易时段席位 watcher |
| `GUANLAN_SNAPSHOT_ARCHIVE` | `1` | 收盘快照归档 |

9 月 8 日文档复核：原表中的 `GUANLAN_MCP_WRITE=0` 已过时；当前实际部署由
`scripts/check_9999.ps1` 的封闭配置设为 `1`，不是由凭据文件开启。本次仅校正文档，
未改变权限。Codex 工作空间工具仍走短期、服务端绑定的 `/gl-mcp-codex` scope；
系统写门开启不代表 Codex 可调用公共 MCP 的所有写工具。

只验证配置而不碰健康检查、heartbeat 或服务进程，可运行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass `
  -File scripts/check_9999.ps1 -ValidateRuntimeProfileOnly
```

该命令只返回以上非秘密配置。`var/secrets.env` 中若仍有旧的 feature/runtime
行，凭据加载器会继续拒绝并在 `/runtime/secrets-env-status` 显示
`blocked_entries`；它们不再决定 9999 的运行姿态。持有该文件权限的运维者应先
执行只读预览，再显式应用迁移：

```powershell
python scripts/migrate_runtime_gates_from_secrets.py
python scripts/migrate_runtime_gates_from_secrets.py --apply
```

迁移器只删除复核清单中的 11 个精确 key，保留全部凭据和其他行，原文件先备份到
`var/runtime-gate-migration/`；备份和替换候选都先收紧为当前用户、SYSTEM、
Administrators 三方 ACL，任一 ACL 操作失败即删除未受保护的临时备份并拒绝切换。
随后才在同目录原子替换；输出只含 key 名和计数，不回显任何值。重复的旧开关会
阻断应用，不能用通用 dotenv 工具重写或回显整份文件。

## 只读状态

服务提供 `GET /runtime/secrets-env-status`。响应只包含状态、计数以及已加载、
已存在或被拒绝的 key 名；不包含值、原始行、文件路径或解析异常文本。
`blocked_entries` 表示文件需要运维修正，不能把被拒绝的设置视为已生效。

若显式设置 `GUANLAN_TESTING=1`，服务的默认加载动作直接返回
`skipped_testing`，不会打开真实 `var/secrets.env`。测试凭据解析器时必须传入
测试临时文件。

## pytest 输出根

`tests/conftest.py` 在收集测试模块之前创建一个进程级临时目录，同时设置
`GUANLAN_TESTING=1` 和绝对的 `GUANLAN_TEST_ROOT`。随后
`configure_test_output_environment()` 把共享 ASGI app、Console、工作流、
报告、研究、席位、Events、快照和已审计的模块级输出统一重定向到这个目录，
并关闭服务后台任务和 Events 写闸。函数级 fixture 仍可把单项路径进一步缩小
到各自的 `tmp_path`。

测试模式不会靠 `pytest` 进程名自动猜测。只有 `GUANLAN_TESTING=1` 且
`GUANLAN_TEST_ROOT` 是仓库外的绝对路径时才会启用；缺根、相对路径或指向
仓库/`var`/`.data` 的根会 fail closed。这样既保留一个跨测试共享的
`guanlan_v2.server.app`，又避免导入期间建立生产 Console/工作流目录。

## 仍保持黄色的旧输出边界

已审计的 `server` 导入与默认 app 构造不会写生产 `var`/`.data`。不过少数
vendored engine/legacy 操作仍有自己的输出约定，例如用户目录下的日常工具
缓存、记忆审计/告警存储，以及显式生成任务的旧 `out/` 或模型训练产物。它们
不在导入时执行，也不影响本次测试隔离结论；在这些显式操作全部改接统一
runtime root 并补回归前，应继续标为黄色，而不是宣称全局输出已完全隔离。
