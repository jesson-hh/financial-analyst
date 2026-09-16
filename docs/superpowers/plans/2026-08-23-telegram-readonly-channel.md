# 观澜 Telegram 只读会话通道 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** 在不改变现有帷幄、落子、研究空间、SSE、转向和归档语义的前提下，为一个帷幄对话建立严格一对一的 Telegram Bot 只读镜像；支持 Agent 主导的安全配对、最终文字、显式图片、会话级定时任务和可恢复投递。

**Architecture:** ConsoleStore 仍是唯一会话事实源。新增通用 channels 包，以 SQLite 原子维护 Bot、绑定、配对、投影游标、Outbox、自动化和审计；Token 只进 Windows CurrentUser DPAPI 密钥仓。Console 只新增一次性 agent_final 事件和受控系统轮次接缝。Projector 从持久事件前向扫描，Outbox worker 再通过 Telegram Adapter 发送；任何 Telegram 入站都在配对边界终止。

**Tech Stack:** Python 3.11、FastAPI、stdlib sqlite3、httpx、zoneinfo、pytest、httpx.MockTransport；Windows 条件依赖 pywin32；现有无打包器 React JSX 全局脚本和 CSS。

---

## 0. 开工前冻结

**Authoritative design:** docs/superpowers/specs/2026-08-22-telegram-readonly-channel-design.md

**必须保持：**

- 帷幄是唯一 Agent 输入入口；Telegram 永不反向控制 Agent。
- 一个会话最多一个 Bot，一个 Bot 最多一个会话，一个私聊接收者。
- 普通镜像只含电脑端可见 user_msg 和完整提交的 agent_final。
- agent_delta、tool_call、tool_result、reasoning、进度、确认卡、内部错误一律不自动外发。
- 图片只接受当前会话 MediaStore 的不透明 media_id，并走显式发布。
- Token 不得进入 Prompt、事件、Buddy 状态、Outbox、DTO、日志或异常。
- /console/send 与 /console/steer 必须共用同一落事件前 Token 截流；运行中转向不能成为秘密旁路。
- 配对码一小时；批准后的绑定永久，直到解绑、Token 身份改变或用户暂停。
- 绑定从批准后的下一条可见事件开始，不补历史。
- Telegram 故障不得阻塞 ConsoleStore 落盘或 Agent 运行。
- 结果不确定进入 uncertain，不盲重试，不伪称 exactly-once。
- 未启用 GUANLAN_TELEGRAM_ENABLED=1 时，现有行为保持不变。

**本计划冻结的默认值：**

- SQLite: var/console/channels.sqlite3，WAL、foreign_keys=ON、synchronous=FULL、busy_timeout=5000。
- 密钥: Windows CurrentUser DPAPI；非 Windows 或账户无法解密时 fail closed。
- 配对码: 8 位大写，排除 0/O/1/I，scrypt 加盐摘要，一小时过期，每 Bot 最多 3 个 pending。
- Projector 批量 200；Outbox/Projector 空闲轮询 0.5 秒。
- getUpdates timeout 15 秒；poller lease 60 秒，每 20 秒续租。
- 瞬态重试 2 秒指数退避，最高 300 秒，加 0–20% jitter；429 尊重 1–3600 秒 retry_after。
- 连续 5 次 retryable 失败熔断 60 秒；half-open 再失败倍增，最高 900 秒。
- 自动化 tick 15 秒；漏跑只补最新一次，grace 6 小时。
- 首期日程只支持 once、daily、weekdays、trading_days；不引入任意 cron。trading_days 必须接真实 A 股交易日历。

**工作树纪律：**

- 当前已有用户/运行时改动：.data/wisdom、guanlan_v2/strategy/_provenance.json、ui/fundflow 和 tests/test_fundflow_ui.py。任何 Telegram 提交都不得包含这些路径。
- 每个提交使用显式 pathspec；禁止 git add -A。
- 每个任务严格红测、确认失败原因、最小实现、绿测、精确提交。

---

## Task 1: 通用 Channel SQLite 事实库与领域状态机

**Files:**

- Create: guanlan_v2/channels/__init__.py
- Create: guanlan_v2/channels/models.py
- Create: guanlan_v2/channels/store.py
- Test: tests/test_channels_store.py

**Domain contract:**

    BotAccount:
      draft -> awaiting_pairing -> active <-> paused
      awaiting_pairing|active|paused -> poll_conflict
      draft|awaiting_pairing|active|paused|poll_conflict
        -> credential_invalid|credential_unavailable|credential_cleanup_pending|disconnected

    ChannelBinding:
      active <-> paused -> disconnected

    PairingRequest:
      pending -> approved|dismissed|expired

    Outbox:
      pending -> sending -> sent|retry_wait|uncertain|terminal
      retry_wait -> sending
      pending|retry_wait -> cancelled
      uncertain -> sent|pending|cancelled
      terminal -> pending|cancelled

    AutomationRun:
      queued -> running -> done|error|missed|cancelled

- [ ] **Step 1: 写缺失模块的红测。**

至少覆盖：建库幂等、WAL/FULL/foreign key、两个活跃绑定的 session_id 冲突、两个活跃绑定的 bot_account_id 冲突、活跃 telegram_bot_id 冲突、批准同一 request 幂等、不同 request 并发批准只能一个成功、Outbox 幂等键、每 binding sequence 单调、投影插入和 cursor 前进同事务、AutomationRun 唯一键。

~~~python
def test_active_binding_is_one_to_one_in_both_directions(tmp_path):
    store = ChannelStore(tmp_path / "channels.sqlite3")
    bot_a = store.create_bot("甲")
    bot_b = store.create_bot("乙")
    store.activate_test_bot(bot_a, telegram_bot_id="101")
    store.activate_test_bot(bot_b, telegram_bot_id="102")
    store.bind_for_test("cs_a", bot_a, user_id="1", chat_id="1", sync_from_event_id=7)
    with pytest.raises(ChannelConflict):
        store.bind_for_test("cs_a", bot_b, user_id="2", chat_id="2", sync_from_event_id=8)
    with pytest.raises(ChannelConflict):
        store.bind_for_test("cs_b", bot_a, user_id="1", chat_id="1", sync_from_event_id=8)


def test_project_and_advance_cursor_is_atomic(tmp_path):
    store = ChannelStore(tmp_path / "channels.sqlite3")
    binding = seeded_binding(store, session_id="cs_a", cursor=10)
    store.project_event(binding.id, source_event_id=11, parts=[
        OutboundPart(kind="text", payload_part=0, text="A"),
        OutboundPart(kind="text", payload_part=1, text="B"),
    ])
    assert store.binding(binding.id).projection_cursor_event_id == 11
    assert [x.sequence for x in store.outbox_for(binding.id)] == [1, 2]
    store.project_event(binding.id, source_event_id=11, parts=[
        OutboundPart(kind="text", payload_part=0, text="A"),
        OutboundPart(kind="text", payload_part=1, text="B"),
    ])
    assert len(store.outbox_for(binding.id)) == 2
~~~

- [ ] **Step 2: 运行红测并核对不是 collection error。**

    python -m pytest -q tests/test_channels_store.py

Expected: ModuleNotFoundError: guanlan_v2.channels。

- [ ] **Step 3: 实现 enums/dataclasses 和完整 schema。**

store.py 的单一初始化 SQL 必须包含以下表和索引：

    bot_accounts(
      id PK, channel, draft_session_id, display_name,
      telegram_bot_id, telegram_username, credential_id,
      credential_revision, status, update_offset, poll_owner, poll_lease_until,
      last_verified_at, last_error_code, created_at, updated_at
    )

    channel_bindings(
      id PK, channel, session_id, bot_account_id FK, telegram_user_id,
      telegram_chat_id, telegram_username, telegram_display_name, status,
      pause_reason, paired_at, sync_from_event_id,
      projection_cursor_event_id, last_sent_at, last_error_code,
      paused_at, disconnected_at, created_at, updated_at
    )

    pairing_requests(
      id PK, bot_account_id FK, session_id, telegram_update_id,
      telegram_user_id, telegram_chat_id, telegram_username,
      telegram_display_name, code_salt BLOB, code_digest BLOB, code_hint,
      status, created_at, expires_at, last_seen_at, approved_binding_id
    )

    outbox(
      id PK, binding_id FK, source_session_id, source_event_id,
      payload_part, batch_id, sequence, kind, text, media_id, caption,
      state, attempts, next_attempt_at, lease_owner, lease_until,
      telegram_message_id, last_error_class, last_error_at,
      created_at, sent_at
    )

    automations(
      id PK, name, session_id, schedule_kind, local_time, run_at,
      timezone, prompt_text, skill_refs_json, media_policy, enabled,
      pause_reason, next_run_at, last_run_at, last_status,
      misfire_grace_seconds, created_by, confirmed_at, created_at, updated_at
    )

    automation_runs(
      id PK, automation_id FK, scheduled_for, status, turn_id,
      final_event_id, lease_owner, lease_until,
      started_at, ended_at, error_class
    )

    channel_audit(
      id INTEGER PK, ts, event_kind, session_id, bot_account_id,
      binding_id, pairing_request_id, automation_id, outbox_id,
      error_class, safe_details_json
    )

    credential_cleanup_queue(
      credential_id PK, bot_account_id, reason, attempts,
      next_attempt_at, last_error_class, created_at
    )

唯一索引必须在 SQLite 层实现，不依赖 Python 先查：

    CREATE UNIQUE INDEX uq_live_binding_session
    ON channel_bindings(session_id)
    WHERE status IN ('active', 'paused');

    CREATE UNIQUE INDEX uq_live_binding_bot
    ON channel_bindings(bot_account_id)
    WHERE status IN ('active', 'paused');

    CREATE UNIQUE INDEX uq_live_telegram_bot
    ON bot_accounts(telegram_bot_id)
    WHERE telegram_bot_id IS NOT NULL AND status <> 'disconnected';

    CREATE UNIQUE INDEX uq_open_draft_session
    ON bot_accounts(draft_session_id)
    WHERE draft_session_id IS NOT NULL AND status <> 'disconnected';

    CREATE UNIQUE INDEX uq_outbox_source_part
    ON outbox(binding_id, source_event_id, payload_part);

    CREATE UNIQUE INDEX uq_automation_occurrence
    ON automation_runs(automation_id, scheduled_for);

draft_session_id 是配对前唯一的会话归属，批准后仍保留审计关联；current_draft、归档和 hard-delete blocker 不得猜 pairing request。credential_revision 每次 credential 成功切换递增，供联网 getMe 后的事务 CAS。所有批准、claim、project_event、状态决议方法使用 BEGIN IMMEDIATE；所有时间以 UTC aware ISO 存储；Telegram 数字 ID 以 TEXT 保存。

- [ ] **Step 4: 实现安全审计白名单。**

ChannelStore.audit 只允许结构化 ID、状态、时间和 error_class；拒绝 token、code、text、caption、path、url 等键。测试 safe_details_json 不含消息全文。

- [ ] **Step 5: 跑绿测和 SQLite 结构检查。**

    python -m pytest -q tests/test_channels_store.py
    python -m compileall -q guanlan_v2/channels

- [ ] **Step 6: 精确提交。**

    git add -- guanlan_v2/channels/__init__.py guanlan_v2/channels/models.py guanlan_v2/channels/store.py tests/test_channels_store.py
    git commit -m "feat(channels): add durable one-to-one channel registry"

---

## Task 2: DPAPI SecretStore、全链路脱敏与 Telegram HTTP Adapter

**Files:**

- Modify: pyproject.toml
- Create: guanlan_v2/channels/secrets.py
- Create: guanlan_v2/channels/telegram.py
- Test: tests/test_channels_secret_store.py
- Test: tests/test_channels_telegram.py

- [ ] **Step 1: 写 SecretStore 红测。**

覆盖：MemoryProtector 往返、磁盘只有密文、credential_id 正则与不可猜、原子替换、删除、解密失败映射 credential_unavailable、API 对象没有 reveal/list plaintext、非 Windows 无明文降级、真实 Token 不出现在 caplog 或异常。另测父目录/目标 reparse 或 symlink 拒绝、路径逃逸拒绝、启动清理过期 tmp、只枚举合法 credential ID 而不解密、replace 失败不破坏旧密文、写失败不留半文件。

~~~python
class FakeProtector:
    def protect(self, value: bytes) -> bytes:
        return b"sealed:" + value[::-1]

    def unprotect(self, value: bytes) -> bytes:
        if not value.startswith(b"sealed:"):
            raise SecretUnavailable("wrong account")
        return value[7:][::-1]


def test_secret_file_never_contains_plaintext(tmp_path):
    secrets = SecretStore(tmp_path / "secrets", protector=FakeProtector())
    raw = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
    credential_id = secrets.put(raw)
    assert secrets.get(credential_id) == raw
    assert raw.encode() not in next((tmp_path / "secrets").iterdir()).read_bytes()
~~~

- [ ] **Step 2: 写 Telegram Adapter 红测。**

使用 httpx.MockTransport 覆盖 getMe、getUpdates、sendMessage、sendPhoto、401、403、409、429 retry_after、5xx、ConnectError、ConnectTimeout、PoolTimeout、WriteTimeout、ReadTimeout、ReadError、RemoteProtocolError、2xx 坏 JSON。分别断言发送操作和只读/长轮询操作的分类；生产 URL 永远是 https://api.telegram.org，构造器没有 base_url 参数，异常和所有 logger 均无 raw token。

~~~python
@pytest.mark.anyio
async def test_adapter_uses_fixed_official_host_and_redacts_token(caplog):
    seen = []
    async def handler(request):
        seen.append(request.url)
        return httpx.Response(200, json={"ok": True, "result": {"id": 42, "username": "bot"}})
    token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_abcdefghi"
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    adapter = TelegramAdapter(client=client)
    assert (await adapter.get_me(token)).bot_id == "42"
    assert seen[0].host == "api.telegram.org"
    assert token not in caplog.text
~~~

- [ ] **Step 3: 运行红测。**

    python -m pytest -q tests/test_channels_secret_store.py tests/test_channels_telegram.py

- [ ] **Step 4: 增加 Windows 条件依赖并实现 SecretStore。**

pyproject.toml 追加：

    "pywin32>=306; sys_platform == 'win32'",

secrets.py 提供 SecretProtector Protocol、WindowsDpapiProtector、SecretStore、SecretUnavailable。credential_id 仅接受 cred_[0-9a-f]{32}；父目录和目标存在 reparse/symlink 时 fail closed，目标必须 resolve 在配置根内。Windows 实现调用 win32crypt.CryptProtectData/UnprotectData，使用 CurrentUser 和 CRYPTPROTECT_UI_FORBIDDEN；密文写到 var/console/channel_secrets/<credential_id>.bin.tmp 后 fsync/原子 replace，并 best-effort 收紧当前用户权限。启动只清理本目录内匹配格式的过期 tmp。SecretStore 可向 Runtime 提供仅含合法 opaque ID 与 mtime 的内部枚举，用于孤儿密文对账，但不提供明文列表或批量解密。导入失败、非 Windows、账户改变或密文损坏一律抛 SecretUnavailable，不得回退明文或 keyring 偶然后端。

Python str、httpx URL 和第三方库可能复制 Token，实施只能缩短局部变量生命周期、尽快丢引用并保证日志/异常脱敏；不得宣称完成内存 zeroization。

- [ ] **Step 5: 实现 Telegram Adapter 和错误分类。**

telegram.py 只暴露安全 DTO 与分类异常：

    TelegramRetryable(retry_after=None)
    TelegramUncertain
    TelegramCredentialInvalid
    TelegramTargetUnavailable
    TelegramPollConflict
    TelegramProtocolError

生产 API URL 在方法内部固定拼接；不把 URL 放进异常。parse_mode 省略，文字按纯文本发送。send_photo 只接受 bytes、文件名、mime 和安全 caption。

分类按 operation 冻结，TelegramUncertain 只能由 sendMessage/sendPhoto 产生：

- getMe/getUpdates 的 ConnectError、ConnectTimeout、PoolTimeout、WriteTimeout、ReadTimeout、ReadError、RemoteProtocolError、2xx 坏响应 -> retryable 或 protocol_error，不得标 uncertain。
- sendMessage/sendPhoto 的 ConnectError、ConnectTimeout、PoolTimeout -> retryable。
- sendMessage/sendPhoto 的 WriteTimeout、ReadTimeout、ReadError、RemoteProtocolError、2xx 坏响应 -> uncertain。
- 429 -> retryable(retry_after)。
- 401 -> credential_invalid。
- 403、chat not found -> target_unavailable。
- getUpdates 409 -> poll_conflict。
- 明确 5xx 响应 -> retryable。

将 httpx、httpcore 及已存在的 httpcore.* 子 logger effective level 固定为 WARNING；对其现有 handlers 和应用 root handlers 安装 TelegramTokenRedactionFilter，过滤 bot<token> 路径和普通 token 形状。测试在 root caplog DEBUG 下逐条检查 LogRecord.getMessage、args、异常 repr 和 traceback，而不只检查 caplog.text。捕获 httpx 异常后 raise 安全分类 from None。

- [ ] **Step 6: Windows-only DPAPI 冒烟测试。**

测试生成假的高熵测试值，用临时目录往返并立即删除；不得使用真实 Bot Token。非 Windows跳过。

- [ ] **Step 7: 跑绿测并精确提交。**

    python -m pytest -q tests/test_channels_secret_store.py tests/test_channels_telegram.py
    git add -- pyproject.toml guanlan_v2/channels/secrets.py guanlan_v2/channels/telegram.py tests/test_channels_secret_store.py tests/test_channels_telegram.py
    git commit -m "feat(channels): protect Telegram credentials and isolate Bot API"

---

## Task 3: Console 完整最终回复事件与前向事件读取

**Files:**

- Modify: guanlan_v2/console/store.py
- Modify: guanlan_v2/console/api.py
- Modify: tests/test_console_store.py
- Modify: tests/test_console_api.py

- [ ] **Step 1: 写 read_events_forward 红测。**

构造 2,205 个事件，after_id=0、limit=200 时必须返回 1..200，而现有 read_events 继续保留尾部语义。连续分页不得丢 id 空洞后的事件。

~~~python
def test_read_events_forward_returns_oldest_page_after_cursor(tmp_path):
    store, sid = seeded_console(tmp_path)
    for i in range(2205):
        store.append_event(sid, "user_msg", text=str(i))
    page = store.read_events_forward(sid, after_id=0, limit=200)
    assert [e["id"] for e in page] == list(range(1, 201))
    assert store.read_events(sid, after_id=0, limit=200)[0]["id"] == 2006
~~~

- [ ] **Step 2: 写 agent_final 红测。**

覆盖：自然结束只落一条 agent_final；多块最终文本稳定拼接；工具调用前 text 不进入 final；tool/error 使候选失效；取消和被转向旧轮不落 final；续跑新轮只落自己的 final；事件带 turn_id、source_user_event_id、source；UI 仍只有 agent_delta 一份显示。崩溃切口分别覆盖 checkpoint 前、checkpoint 后 final 前、final 后 done 前；checkpoint 写入失败必须让本轮失败并保证 agent_final、成功 done 和 Outbox 全部为零。

~~~python
def test_tool_preamble_is_not_committed_as_final(client, store, sid):
    agent = ScriptedAgent([
        TurnEvent("text", "我先查一下"),
        TurnEvent("tool_call", {"name": "stock_brief", "args": {}}),
        TurnEvent("tool_result", {"name": "stock_brief", "content": "ok"}),
        TurnEvent("text", "这是最终结论"),
        TurnEvent("done", None),
    ])
    send_and_wait(client, sid, agent)
    finals = [e for e in store.read_events(sid) if e["type"] == "agent_final"]
    assert [e["text"] for e in finals] == ["这是最终结论"]
~~~

- [ ] **Step 3: 运行红测。**

    python -m pytest -q tests/test_console_store.py tests/test_console_api.py -k "forward or agent_final or committed or restart"

- [ ] **Step 4: 实现前向读取，不改旧 SSE 契约。**

ConsoleStore.read_events_forward 在持锁下顺序读 after_id 之后的最早 limit 条，达到 limit 即停；read_events 原函数不改语义。

- [ ] **Step 5: 在 _run_turn 形成候选最终文本，并按 checkpoint -> final -> done 提交。**

核心逻辑：

    final_chunks = []

    if kind == "text" and payload:
        final_chunks.append(str(payload))
        _emit(sid, "agent_delta", text=str(payload), turn_id=turn_id)
    elif kind in {"tool_call", "tool_result", "error"}:
        final_chunks.clear()
        ...
    elif kind == "done":
        saw_done = True

    if saw_done:
        final_text = "\n".join(final_chunks)
        _checkpoint_agent(
            st, sid, turn_agent,
            completed_turn={
                "turn_id": turn_id,
                "source_user_event_id": user_event_id,
                "source": control.get("source", "user"),
                "final_text": final_text,
            },
        )
        if final_text and not control.get("cancel_requested"):
            final_event = _emit(
                sid, "agent_final",
                text=final_text,
                turn_id=turn_id,
                source_user_event_id=user_event_id,
                source=control.get("source", "user"),
            )

agent_final 必须在 Agent 完整 done 和完整 Buddy checkpoint 之后、task_update done 之前落盘；不得复用 agent_delta。buddy_state 的 completed_turn 是私有崩溃恢复标记，不进入 SSE。checkpoint 抛错时立即走失败收尾，绝不能继续提交 agent_final 或成功 done。

启动扫描对 stale foreground：

1. 已有同 turn agent_final -> 补 done(ok=true, recovered=true) 并置 idle。
2. 没有 final，但 buddy_state.completed_turn.turn_id 匹配 -> 从该私有标记补 agent_final，再补 done(ok=true)。
3. checkpoint 也未完成 -> 按现有 interrupted/cancelled 语义收尾。

这样不会出现事件显示成功而 Buddy 私有协议仍停在旧 checkpoint。

- [ ] **Step 6: 全量 Console 回归。**

    python -m pytest -q tests/test_console_store.py tests/test_console_api.py tests/test_console_pro_ui.py

- [ ] **Step 7: 精确提交。**

    git add -- guanlan_v2/console/store.py guanlan_v2/console/api.py tests/test_console_store.py tests/test_console_api.py
    git commit -m "feat(console): commit final agent replies for durable projection"

---

## Task 4: ChannelRuntime、脱敏状态 API 与 Token 落盘前拦截

**Files:**

- Create: guanlan_v2/channels/runtime.py
- Create: guanlan_v2/channels/api.py
- Create: guanlan_v2/console/request_security.py
- Modify: guanlan_v2/console/api.py
- Test: tests/test_channels_api.py
- Test: tests/test_console_channels.py

**Public API v1:**

    GET  /console/channels/session/{sid}
    POST /console/channels/session/{sid}/request-token
    POST /console/channels/telegram/token
    POST /console/channels/session/{sid}/pause
    POST /console/channels/session/{sid}/resume
    POST /console/channels/session/{sid}/reverify
    POST /console/channels/session/{sid}/replace-token
    POST /console/channels/session/{sid}/disconnect
    POST /console/channels/deliveries/{item_id}/resolve

**Stable redacted status DTO:**

    {
      "available": true,
      "session_id": "cs_...",
      "view_state": "awaiting_token|awaiting_pairing|pairing_pending|active|paused|retrying|uncertain|needs_reconnect|none",
      "draft": {"draft_id": "bot_..."},
      "bot": {"display_name": "...", "username": "..."},
      "recipient": {"user_id": "...", "username": "...", "display_name": "..."},
      "pairing_requests": [{
        "request_id": "pair_...", "user_id": "...", "username": "...",
        "display_name": "...", "code_hint": "…AB",
        "created_at": "...", "expires_at": "..."
      }],
      "counts": {"pending": 0, "retry_wait": 0, "uncertain": 0, "terminal": 0},
      "warnings": [],
      "recent_deliveries": [{
        "outbox_id": "...", "kind": "text|media", "state": "...",
        "media_id": null, "created_at": "...", "sent_at": null,
        "attempts": 0, "error_class": null
      }],
      "automations": [{
        "automation_id": "auto_...", "name": "...", "schedule_kind": "...",
        "next_run_at": "...", "last_run_at": null,
        "status": "enabled|paused|blocked|error", "pause_reason": null
      }]
    }

draft、bot、recipient 在不适用时为 null；pairing_requests、recent_deliveries、automations 永远是数组。draft_id、request_id、outbox_id、automation_id 都是不透明本地 ID，不是 Token、credential_id、Telegram Bot ID 或任意文件路径。view_state 是后端按固定优先级派生的组合视图，不新增领域枚举：needs_reconnect > uncertain > retrying > paused > pairing_pending > awaiting_pairing > awaiting_token > active > none。credential_invalid、credential_unavailable、Bot identity changed 和 poll_conflict 统一派生为 needs_reconnect，并在 warnings 给出安全 error_code；credential_cleanup_pending 只进入 warnings，不伪装成已完全解绑。feature gate 关闭或 endpoint 不可用时前端归一为 available=false、view_state=none，不显示故障徽章。

- [ ] **Step 1: 写 API 红测。**

覆盖：同源允许，Origin:null、跨源和 Sec-Fetch-Site:cross-site 拒绝；敏感 POST 没有一次性 ticket 拒绝；ticket 绑定 sid/action/subject、两分钟过期、只能消费一次；status DTO 的 nullable/数组形状稳定，提供 UI 必需的不透明 draft/request/outbox/automation ID，但不含 credential_id、Token、Telegram Bot ID、消息全文、caption、本地路径；任何响应不回显提交 Token。Token endpoint 的超大 body、坏 JSON、缺字段、非字符串、默认验证错误路径都只能返回固定 error_code，禁止 FastAPI 422 的 input/body 回显。display_name、automation name/prompt、Skill 参数、media caption 及 Channel 自由文本一旦命中 Token 形状必须在写 SQLite 前拒绝；测试同时扫描 response.content、caplog、异常 repr 和 traceback。

- [ ] **Step 2: 写 /console/send 与 /console/steer Token 截流红测。**

形似 Token 的 text 必须在 append_event 前进入同一秘密截流器。当前有 draft 且请求携带绑定 sid + action=`telegram:submit-token` + draft_id 的有效单次 ticket 时，才转交 runtime，事件只落：

    {
      "type": "user_msg",
      "text": "[Telegram Bot Token 已安全提交]",
      "channel_projection": "never",
      "secret_placeholder": true
    }

若该路径需要启动 Agent，Agent 收到的 turn text 也只能是占位符；专用 Token endpoint 只落占位和刷新状态，不额外启动一轮 Agent。无 draft 时返回 reason_code=telegram_token_needs_draft；有 draft 但缺少/错误/过期 ticket 时返回 reason_code=telegram_token_secure_submit_required。两种拒绝都不落 user_msg、不调用 Agent、不消费或替换 credential。测试还覆盖 ticket 重放、跨 sid/draft 和 response replay，扫描 events.jsonl、buddy_state.json、channels.sqlite3、Outbox、HTTP 响应和 caplog，raw token 出现次数必须为 0。

/steer 在任何 user_msg、steers control 或取消请求前执行同一 helper。有 draft 且 ticket 有效时安全提交后直接返回 secret_submitted=true，不打断当前 Agent、不落转向事件；无 draft或 ticket 无效时安全拒绝且当前轮继续原样运行。覆盖 busy composer 和 ConfirmModal 转向框两个入口。

- [ ] **Step 3: 运行红测。**

    python -m pytest -q tests/test_channels_api.py tests/test_console_channels.py -k "origin or ticket or token or status"

- [ ] **Step 4: 实现 ChannelRuntime 的同步领域门面。**

runtime.py 构造时显式注入受限 ConsoleStore/EventReader、ChannelStore、SecretStore、TelegramAdapter、MediaStore，并提供：

    create_draft(session_id, display_name)
    submit_token(session_id, draft_id, token)
    status_for_session(session_id)
    summary_for_session(session_id)
    pause/resume/reverify/replace_token/disconnect
    resolve_delivery
    current_draft
    looks_like_telegram_token

Agent 和 API 都调用同一门面；没有任何调用方直接改 SQLite。ConsoleStore 注入从 Task 4 起就用于读取当前尾事件 ID和前向事件，不等到自动化 Task。Token 校验 getMe 成功后只持久 Bot ID、username、credential_id 和安全状态。getMe 失败只返回安全 error_code。

边界冻结：纯 SQLite/文件方法是同步方法并由 API threadpool或 runtime 内 asyncio.to_thread 调用；getMe、poll、发送和 runtime loops 是 async。不得在事件循环里直接执行 busy_timeout=5000 的 SQLite、DPAPI 或 MediaStore 文件 I/O。

Token 提交/替换采用补偿式顺序：

1. 对 raw token 调 getMe，失败时零 SecretStore 写入。
2. getMe 成功后写新 credential。
3. BEGIN IMMEDIATE，以 bot_account_id + credential_revision 做 CAS，更新 credential_id、Bot identity 并 revision+1。
4. SQLite 提交失败时删除新 credential；删除失败进入 credential_cleanup_queue。
5. replace 提交成功后再删除旧 credential；删除失败保留新连接但 status DTO 显示 cleanup_pending，后台重试。
6. replace 任一步失败都继续使用旧 credential。
7. disconnect、放弃 draft 和允许 hard delete 前删除不再使用的 credential；失败时 Bot 进入 credential_cleanup_pending，不能宣称完全解绑。

测试 getMe 失败无密钥、DB 失败无 orphan、replace 失败旧 Token 仍有效、disconnect cleanup 失败显形。

getMe 必须在 SQLite 事务外完成；配对批准同样先读取 credential_revision、解密并 getMe，再在 BEGIN IMMEDIATE 内 CAS 复验 revision + telegram_bot_id，事务内禁止网络 I/O。

- [ ] **Step 5: 实现短期一次性 ActionTicketStore。**

票据仅在进程内保存随机值的 SHA-256 摘要、sid、action、subject_id、expires_at；consume 使用 compare_digest 并立即删除。request-token 本身只接受可信同源请求，不要求票据以免形成循环；状态 GET 不自动签发通配票据，前端按具体动作调用 request-token。该存储也供既有 /console/confirm 使用：所有 Telegram/automation 工具的肯定确认必须消费绑定 sid + `confirm:<tool_name>` + turn_id 的两分钟单次票据，拒绝/停止不需要票据。

- [ ] **Step 6: 实现 channel API 与更严格请求边界。**

把现有纯请求判断提取到 console/request_security.py，console/api.py 保留兼容 wrapper 和原错误文案；channels/api.py 复用同一判断并叠加可信 Sec-Fetch-Site。敏感端点同时要求同源规则和 action ticket。Token body 只在局部变量中存在，finally 释放引用；不记录 request body 或异常 URL。

Token endpoint 使用 Request 手工限制 body 大小和解析 JSON，所有 malformed/missing/type 错误返回固定安全 JSON；不得让 FastAPI/Pydantic 默认 422 把 body.input 回显。共享 reject_secret_shape 应用于 Bot display_name、automation name/prompt、Skill 参数、media caption 和所有 Channel SQLite/Outbox 自由文本。普通 send/steer 含恰好一个嵌入式 Token 时整条按秘密处理、丢弃周边文字并落安全占位；含多个 Token 时安全拒绝且不回显。

- [ ] **Step 7: 给 build_console_router 增加可选 channel_runtime=None，并集中秘密截流。**

默认 None 路径保持现有测试逐字行为。新增 _intercept_channel_secret，由 /send 和 /steer 在 text 取值后、任何事件、control 变更或 Agent 调用前共同调用。sessions_list 只动态装饰脱敏 channel_summary，不写 ConsoleStore meta。

- [ ] **Step 8: 跑回归并精确提交。**

    python -m pytest -q tests/test_channels_api.py tests/test_console_channels.py tests/test_console_api.py
    git add -- guanlan_v2/channels/runtime.py guanlan_v2/channels/api.py guanlan_v2/console/request_security.py guanlan_v2/console/api.py tests/test_channels_api.py tests/test_console_channels.py
    git commit -m "feat(channels): add secure local channel control surface"

---

## Task 5: OpenClaw 式 Telegram 私聊配对与永久一对一绑定

**Files:**

- Create: guanlan_v2/channels/pairing.py
- Modify: guanlan_v2/channels/runtime.py
- Modify: guanlan_v2/channels/store.py
- Test: tests/test_channels_pairing.py

- [ ] **Step 1: 写配对红测。**

覆盖：字符集无 0/O/1/I、长度 8、磁盘无明文码、TTL 恰好一小时、每 Bot 最多 3 pending、只接受 private chat、同一 telegram_update_id 幂等、pending 才解释入站、active/paused 普通入站只推进 offset且 ConsoleStore/Agent factory/审计正文均零调用。已接受、已忽略和明确拒绝的 update 必须推进 offset；配对码发送发生 retryable pre-send 失败时必须保留 offset 供同一 update 重放。同一 chat 再次私聊时 dismiss 旧 request 并签发新码；三个不同 chat 已占满时拒绝第四个并给安全提示，电脑端可按 request_id dismiss。另用故障注入覆盖 sendMessage 429/5xx/明确 4xx/ambiguous、成功发送后事务提交前崩溃及重启重放，证明不会提交“用户永远收不到”的码，也不会误认旧码。

~~~python
@pytest.mark.anyio
async def test_bound_inbound_is_consumed_without_agent_or_console_write(tmp_path):
    runtime, telegram, console, agent_factory = seeded_active_runtime(tmp_path)
    telegram.queue_update(private_text_update(77, "让 Agent 买入"))
    await runtime.pairing.poll_once(runtime.active_bot_id)
    assert runtime.store.bot(runtime.active_bot_id).update_offset == 78
    assert console.all_events() == []
    assert agent_factory.calls == []
~~~

- [ ] **Step 2: 写并发批准红测。**

当前 sid、draft Bot、配对请求、code、私聊、getMe Bot ID 必须同时吻合。两个会话争同 Bot、两个 Bot 争同会话、过期码、错码、群聊、Bot 身份变化均拒绝。重复批准同一 request 幂等返回原 binding。覆盖 approve 与 replace-token 并发：事务 CAS 只允许 getMe 时读取的 credential_revision 成功。

- [ ] **Step 3: 运行红测。**

    python -m pytest -q tests/test_channels_pairing.py

- [ ] **Step 4: 实现配对码和 request 事务。**

alphabet 固定为：

    23456789ABCDEFGHJKLMNPQRSTUVWXYZ

生成码后用 os.urandom(16) salt 和 hashlib.scrypt 摘要；数据库只存 salt、digest、最后两位 hint。为避免“request/offset 已提交但码尚未发送”的永久坏状态，处理顺序冻结为：

1. 内存生成 code/salt/digest。
2. 调 sendMessage，文案注明“如收到多条，以最新一条为准”。
3. send 成功或 ambiguous 时，事务内持久 request 摘要并推进 offset；ambiguous request 仍可批准，因为用户可能已收到。
4. 明确 pre-send/retryable 失败时不推进 offset，按 poll backoff 重试。
5. 明确 Telegram 拒绝时推进 offset但不创建 request，用户需重新私聊。

若进程在 send 成功后、事务提交前崩溃，update 会重放并产生一个新码；旧码不入库、无效，最新 Bot 消息明确覆盖它。方法返回后不保留明文码。

- [ ] **Step 5: 实现每 Bot poller 和 lease。**

PairingSupervisor 每 2 秒对比数据库 Bot 集合：为新 awaiting_pairing/active/paused 且凭证有效的 Bot 动态起 poller；Token 替换、credential_invalid、disconnect 后取消对应 task。Token 提交成功主动 signal supervisor，不等待下一轮。lease 60 秒、20 秒续约；getUpdates offset 持久。409 标 poll_conflict 并停止该 Bot；不影响其他 Bot。credential_invalid/unavailable 停止网络请求但保留绑定。每个 poller finally 释放自己的 lease；runtime.stop 先取消并 await 全部 poller，再释放 supervisor lease，立即重启不等待 60 秒。

- [ ] **Step 6: 实现 approve_pairing。**

事务外读取 credential_revision、短暂解密并调用 getMe；BEGIN IMMEDIATE 内 CAS 复验 revision、telegram_bot_id、request、expiry、scrypt digest 和双向唯一索引，事务内不得 await 或网络 I/O。sync_from_event_id 和 projection_cursor_event_id 都取批准前 ConsoleStore 当前最后事件 ID。批准后 Bot active、request approved、其余 pending dismissed，绑定永久落盘。若 Token identity changed 后重新配对，原 paused binding 在同事务内更新 recipient、sync boundary 和状态，不并存第二条 live binding。

同一领域服务实现 dismiss_pairing(session_id, request_id)，只能处理当前会话当前 Bot 的 pending request；不接受 Bot/chat/user 参数。

- [ ] **Step 7: 跑绿测和精确提交。**

    python -m pytest -q tests/test_channels_pairing.py tests/test_channels_store.py tests/test_channels_telegram.py
    git add -- guanlan_v2/channels/pairing.py guanlan_v2/channels/runtime.py guanlan_v2/channels/store.py tests/test_channels_pairing.py
    git commit -m "feat(channels): add private-chat pairing and permanent binding"

---

## Task 6: Agent Prompt、窄工具面、确认卡脱敏与 MCP 隔离

**Files:**

- Create: guanlan_v2/channels/agent_tools.py
- Modify: guanlan_v2/console/tools.py
- Modify: guanlan_v2/console/api.py
- Modify: guanlan_v2/glmcp/tooltable.py
- Modify: scripts/gen_agent_interface_doc.py
- Regenerate: docs/agent_data_interfaces.md
- Test: tests/test_console_telegram_tools.py
- Modify: tests/test_console_tools.py
- Modify: tests/test_console_api.py
- Modify: tests/test_agent_interface_doc.py

**Agent tool names:**

    ww_telegram_connect
    ww_telegram_status
    ww_telegram_pair_status
    ww_telegram_pair_approve
    ww_telegram_pair_dismiss
    ww_telegram_pause
    ww_telegram_resume
    ww_telegram_disconnect
    ww_telegram_publish
    ww_automation_skills
    ww_automation_list
    ww_automation_upsert
    ww_automation_pause
    ww_automation_resume
    ww_automation_delete

- [ ] **Step 1: 写工具边界红测。**

断言任何 schema 都没有 token、credential_id、session_id、bot_id、chat_id、user_id、recipient 或 path。所有写工具限定 CTX_SID；没有 ContextVar 时诚实失败。pair approve/dismiss、pause/resume/disconnect/publish 和 automation 写操作 confirm=True；status/list/skills confirm=False。pair dismiss 只接受当前 Bot 的 opaque pairing_request_id，用于清理错误/恶意 pending 请求。所有 Telegram/automation 肯定确认还需一次性 action ticket；缺票据、过期、重复使用、跨 sid、跨 turn_id 或跨 tool_name 均不得唤醒确认 Future，也不得执行工具。

- [ ] **Step 2: 写确认事件脱敏红测。**

ww_telegram_pair_approve 的 code 可以进入 Agent 工具参数，但 confirm_request 的 args 必须删除 code，只留 public_details.code_hint。通用 ConfirmModal 数据里不得持久化明文配对码。由于产品明确要求用户在普通帷幄消息里把临时码告诉 Agent，原始 user_msg、Buddy 用户消息和 Buddy tool-call protocol 可以在一小时有效期内包含该码；Channel DB、confirm 事件、审计、Outbox、日志和 API DTO 仍禁止明文码。Token 从不允许成为工具参数。

- [ ] **Step 3: 写 MCP 隔离红测。**

全部 CHANNEL_TOOL_NAMES 必须进入 guanlan_v2.glmcp.tooltable._EXCLUDED。外部 ChatGPT MCP 不能借这些 console-session 工具选择任意会话/Bot/接收者。

- [ ] **Step 4: 运行红测。**

    python -m pytest -q tests/test_console_telegram_tools.py tests/test_console_tools.py tests/test_console_api.py tests/test_agent_interface_doc.py

- [ ] **Step 5: 实现 CTX_CHANNEL_RUNTIME 和 agent_tools。**

_run_turn 与后台 review 一样 set/reset ContextVar。所有 impl 只取当前 sid 和 runtime。ww_telegram_connect 只接 display_name，创建 draft 后返回安全状态，不接 Token。ww_telegram_pair_approve 只接 code，真正执行仍经现有 confirm gate。

- [ ] **Step 6: 在 _confirm_extras 增加 Telegram/automation 专用预览。**

事件只白名单展示：

- Pair: Bot 自定义名、真实 username、Telegram 用户名/数字 ID、请求/过期时间、code hint、永久一对一说明。
- Publish: 当前 MediaStore 缩略信息、caption、唯一 Bot；不展示路径。
- Automation: 完整时间、时区、Prompt 摘要、skill refs、媒体策略。

新增 _public_confirm_args，敏感工具绝不走 _safe(args) 原样落盘。

确认事件同时返回 confirm_kind、public_details、title 和 approve_label。pending 表项扩展为 sid、Future、tool_name、turn_id；/console/confirm 对 Telegram/automation 的肯定 choice 先消费绑定 sid + `confirm:<tool_name>` + turn_id 的 ActionTicket，再唤醒 Future，校验失败只返回固定 reason_code。普通工具的既有确认契约保持不变，拒绝/停止路径也保持可用。Telegram/automation 前端分支与通用 JSON 分支互斥；永久绑定显示“确认永久绑定”，图片显示“确认发送图片”，不得回落到通用“同意 · 一次”且同时打印 args。

- [ ] **Step 7: 更新 _SYSTEM_PROMPT。**

明确写入已确认流程：用户说连接 Telegram 时调用 connect；Token 必须安全卡或由 /send 服务端截流；Agent 永远不能读取/复述 Token；用户给配对码后调用 pair approve；绑定永久且 Telegram 无入站控制权；发图只接受 media_id；定时任务必须复述时间、时区、内容、Skill 和媒体策略；不得声称 exactly-once。

- [ ] **Step 8: 注册工具并显式排除 MCP。**

console/tools.py 的 WW_TOOL_TABLE 引用 channels.agent_tools 的 impl；CONSOLE_ALLOWED 自动派生。glmcp/tooltable.py 从 CHANNEL_TOOL_NAMES 更新 _EXCLUDED，不手抄遗漏。

- [ ] **Step 9: 再生接口文档并跑绿测。**

    python scripts/gen_agent_interface_doc.py
    python -m pytest -q tests/test_console_telegram_tools.py tests/test_console_tools.py tests/test_console_api.py tests/test_agent_interface_doc.py

- [ ] **Step 10: 精确提交。**

    git add -- guanlan_v2/channels/agent_tools.py guanlan_v2/console/tools.py guanlan_v2/console/api.py guanlan_v2/glmcp/tooltable.py scripts/gen_agent_interface_doc.py docs/agent_data_interfaces.md tests/test_console_telegram_tools.py tests/test_console_tools.py tests/test_console_api.py tests/test_agent_interface_doc.py
    git commit -m "feat(channels): add session-scoped Telegram agent controls"

---

## Task 7: 持久 MessageProjector、文字 Outbox 与可靠投递状态机

**Files:**

- Create: guanlan_v2/channels/projector.py
- Create: guanlan_v2/channels/outbox.py
- Modify: guanlan_v2/channels/runtime.py
- Modify: guanlan_v2/channels/store.py
- Test: tests/test_channels_projector.py
- Test: tests/test_channels_outbox.py

- [ ] **Step 1: 写 Projector 红测。**

覆盖：

- V1 普通会话自动镜像只投影 user_msg 和 agent_final；定时任务也先形成普通 Console agent_final。Task 8 的显式、已确认图片发布使用单独的严格分支，不预留其他宽松事件白名单。
- agent_delta、tool_call、普通 tool_result、task_update、confirm、reasoning、error、automation_trigger 全部黑名单。
- channel_projection=never 无条件跳过。
- 绑定前历史不投影。
- 超过 2,000 个积压事件按最早页逐页前进不漏。
- 游标回退、重复扫描、进程重启不重复 Outbox。
- 长文本按段落、换行、空白、字符边界稳定拆分，单片不超过 4,000 字符。
- 每个分片唯一键是 binding_id + source_event_id + payload_part，顺序与 Console 事件一致。

~~~python
def test_projector_whitelist_is_fail_closed(tmp_path):
    console, channels, binding = seeded_projection(tmp_path)
    console.append_event(binding.session_id, "user_msg", text="用户正文")
    console.append_event(binding.session_id, "agent_delta", text="中间文字")
    console.append_event(binding.session_id, "tool_result", summary="内部结果")
    console.append_event(binding.session_id, "agent_final", text="最终回答", turn_id="t1")
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    assert [x.text for x in channels.outbox_for(binding.id)] == ["用户正文", "最终回答"]
~~~

- [ ] **Step 2: 写 Outbox 红测。**

覆盖：每 binding 只 claim 最小未终态 sequence；两个 worker 并发只能一个 claim；不同 binding 可并行；sent/cancelled 才越过；retry_wait、uncertain、terminal 均阻塞后续；启动 sending -> uncertain；sending lease 过期 -> uncertain；runtime.stop 取消 in-flight HTTP -> uncertain；解绑 pending/retry_wait -> cancelled、sending -> uncertain；人工 assume_sent/resend/drop/retry 转换；成功保存 Telegram message_id。

- [ ] **Step 3: 写失败分类、退避和熔断红测。**

使用可注入 clock、random 和 sleeper，固定断言：

    delay = min(300, 2 * 2 ** (attempts - 1))
    jittered = delay * (1 + uniform(0, 0.2))

429 按 retry_after；连续 5 次 retryable 后 circuit_open 60 秒；half-open 仅发 1 条，失败后 cooldown 倍增到 900 秒；成功清零。uncertain 永不自动重发。

- [ ] **Step 4: 运行红测。**

    python -m pytest -q tests/test_channels_projector.py tests/test_channels_outbox.py

- [ ] **Step 5: 实现 fail-closed Projector。**

对每个活跃 binding：

1. 用 ConsoleStore.read_events_forward(cursor, 200) 取最早一页。
2. 对事件应用显式白名单和 channel_projection 字段。
3. 将纯文本确定性拆成 OutboundPart。
4. 在一个 SQLite 事务内 insert-or-ignore Outbox 并推进 cursor。
5. 黑名单事件同样推进 cursor，避免反复扫描。

区分两种暂停：

- intentional_pause/manual/archive：停止新投影，取消 pending/retry_wait；发送中的项转 uncertain。resume 时再次读取当前 Console 尾 ID，并在一个 Channel SQLite 事务内把 sync_from/cursor 重置到该尾部后才转 active，因此暂停期间事件绝不补发。
- fault_pause/credential_invalid/poll_conflict/terminal：停止 claim/发送但保留 cursor 和既有队列，不静默取消可靠投递；修复凭证/冲突后仍需用户对 terminal/uncertain 作出 retry/drop 决议。

disconnected 不生成新 Outbox，但未解决的 uncertain/terminal 仍保留供 status 决议。

- [ ] **Step 6: 实现 OutboxWorker。**

worker 从 ChannelStore claim 每个 binding 的队首；Token 仅在真正发送方法内从 SecretStore 解密。成功、retryable、uncertain、terminal 分别写明确状态和安全审计。任何异常不得影响 ConsoleStore 或其他 binding。

状态决议：

    pending -> sending
    sending + success -> sent
    sending + retryable -> retry_wait
    sending + ambiguous -> uncertain
    sending + auth/target failure -> terminal
    startup sending -> uncertain
    uncertain + assume_sent -> sent
    uncertain + resend -> pending
    uncertain + drop -> cancelled
    terminal + retry -> pending
    terminal + drop -> cancelled

claim 使用 30 秒 lease 并续租；lease 过期的 sending 在任何新 claim 前先转 uncertain。CancelledError 若发生在 sendMessage/sendPhoto 已开始后，finally 必须转 uncertain。Telegram 200 但 ok:false 按 error_code 分类；ok:true 但缺 message_id 视为 uncertain；未知明确 4xx 为 terminal。

- [ ] **Step 7: 为 Runtime 增加独立循环。**

runtime.start 创建 projector 和 outbox task；runtime.stop 设置 stop event、取消并 await。所有 SQLite、ConsoleStore、DPAPI、MediaStore 操作用 asyncio.to_thread，只有 httpx 留在 async loop。循环内单次失败只记安全 error_class 并继续；不得用 import-time daemon thread。

- [ ] **Step 8: 跑绿测和精确提交。**

    python -m pytest -q tests/test_channels_projector.py tests/test_channels_outbox.py tests/test_channels_store.py tests/test_channels_telegram.py
    git add -- guanlan_v2/channels/projector.py guanlan_v2/channels/outbox.py guanlan_v2/channels/runtime.py guanlan_v2/channels/store.py tests/test_channels_projector.py tests/test_channels_outbox.py
    git commit -m "feat(channels): project committed console text through durable outbox"

---

## Task 8: 显式 MediaStore 图片投递

**Files:**

- Modify: guanlan_v2/console/media.py
- Modify: guanlan_v2/console/workspace_tools.py
- Modify: guanlan_v2/channels/agent_tools.py
- Modify: guanlan_v2/channels/projector.py
- Modify: guanlan_v2/channels/outbox.py
- Modify: guanlan_v2/channels/telegram.py
- Test: tests/test_console_media.py
- Test: tests/test_channels_media.py
- Test: tests/test_console_telegram_tools.py

- [ ] **Step 1: 写媒体所有权红测。**

ww_telegram_publish 只接受 media_id 和可选 caption；绝对路径、相对路径、URL、别的会话 media_id、无 session owner 的旧媒体、非 PNG/JPEG/WebP、超过 10 MB 均拒绝。当前会话归档后不能新发布。

- [ ] **Step 2: 写显式/自动边界红测。**

普通 media_image tool_result 永不外发；只有用户明确要求后，ww_telegram_publish 成功返回的 artifact.kind=telegram_media_publish 才能由 Projector 入 Outbox。确认拒绝时零 Outbox。Outbox 只能保存 media_id/caption，不保存 bytes、path 或 Token。

~~~python
def test_ordinary_conversation_image_is_not_telegram_publish(tmp_path):
    console, channels, binding, media_id = seeded_media_runtime(tmp_path)
    console.append_event(binding.session_id, "tool_result", tool="ww_workspace_quant_chart",
                         ok=True, artifact={"kind": "media_image",
                                            "payload": {"media_id": media_id}})
    ConsoleMessageProjector(console, channels).project_once(binding.id)
    assert channels.outbox_for(binding.id) == []
~~~

- [ ] **Step 3: 写 sendPhoto 红测。**

MockTransport 断言 multipart 只含 MediaStore 验证后的 bytes、受控文件名和 mime；caption 不含路径。caption 超过 900 字符时先形成普通 text 分片，再发 photo 的短 caption，原文不静默丢失。

- [ ] **Step 4: 运行红测。**

    python -m pytest -q tests/test_console_media.py tests/test_channels_media.py tests/test_console_telegram_tools.py

- [ ] **Step 5: 增加 MediaStore.load_for_session。**

复用 load 的签名/结构/大小复验，再强制 metadata.session_id == 当前 sid。不得给 Telegram Adapter 任意文件读取接口。

- [ ] **Step 6: 让 Agent 能看见安全 media_id。**

workspace_quant_chart_impl 和 workspace_publish_image_impl 的 ToolResult content 追加不透明 media_id；它不是路径或秘密，供同一 Buddy 会话后续调用 ww_telegram_publish。现有 media_image artifact 和 UI 不变。

- [ ] **Step 7: 实现 telegram_media_publish 事件信封。**

ww_telegram_publish 先验证当前 sid 的活跃唯一绑定和 MediaStore ownership，再返回：

    {
      "kind": "telegram_media_publish",
      "page": None,
      "channel": "telegram",
      "payload": {"media_id": "...", "caption": "..."},
      "ref": "..."
    }

Projector 仅接受 tool=ww_telegram_publish、ok=true 且该 artifact kind 完全匹配的 tool_result；其他所有 tool_result 继续黑名单。

- [ ] **Step 8: 跑绿测和精确提交。**

    python -m pytest -q tests/test_console_media.py tests/test_channels_media.py tests/test_channels_projector.py tests/test_channels_outbox.py tests/test_console_telegram_tools.py
    git add -- guanlan_v2/console/media.py guanlan_v2/console/workspace_tools.py guanlan_v2/channels/agent_tools.py guanlan_v2/channels/projector.py guanlan_v2/channels/outbox.py guanlan_v2/channels/telegram.py tests/test_console_media.py tests/test_channels_media.py tests/test_console_telegram_tools.py
    git commit -m "feat(channels): add explicit session-owned Telegram image delivery"

---

## Task 9: 帷幄通道状态、非历史 Token 卡和管理抽屉

**Files:**

- Create: ui/console/console-channel.jsx
- Create: ui/console/console-channel.css
- Modify: ui/console/console-data.jsx
- Modify: ui/console/console-app.jsx
- Modify: ui/console/console-thread.jsx
- Modify: ui/console/console-rail.jsx
- Modify: ui/console/观澜 · 帷幄.html
- Create: tests/test_console_channel_ui.py
- Modify: tests/test_console_pro_ui.py

- [ ] **Step 1: 写静态 UI 红测。**

至少守护：

- HTML 中 channel CSS 和 JSX 的加载顺序、全部修改 JSX 的版本号。
- Token 卡不来自 wwDeriveItems，不调用 WW.send。
- type=password、autoComplete=off、spellCheck=false。
- Token 只在 WwSecureTokenCard 局部 state，成功/失败 finally 均清空。
- 不写 localStorage、sessionStorage，不 console.log，不把 token 提升到 WeiwoApp。
- sid + generation 双守卫，切会话时旧响应不能串入。
- 同一 sid 的并发状态请求还有单调 request sequence；旧 sequence 不能覆盖新响应。
- 普通 Composer 与 ConfirmModal 转向框共用的 dispatch 在任何 optimistic busy/send/steer 分支前实际调用 Token 检测。
- feature gate 关闭或 status 404 归一为 available=false，不显示错误徽章。
- 标题状态文案及优先级全部存在。
- 已连接严格显示 已连接 Telegram · Bot名称。
- Telegram/automation 确认不用 JSON.stringify(c.args)。
- 专用确认分支只接 public_details，且与通用 args JSON 分支互斥。
- Telegram/automation 的肯定确认会先申请绑定 sid + tool_name + turn_id 的一次性 ticket；前端不缓存、不复用。
- 抽屉无查看 Token。
- disconnect 必须二次确认；replace-token 只打开新安全卡；归档会话全部管理写操作禁用。
- 研报抽屉和通道抽屉互斥。
- 投递状态只在服务端成功响应后重拉，不乐观写 sent。
- recent_deliveries 按 media_id 关联进 item.delivery。
- 保留 function ConfirmModal({ confirm, onChoice, onStop, onSteer }) 和 function WwMediaImageCard({ item }) 两个现有签名。

- [ ] **Step 2: 运行红测。**

    python -m pytest -q tests/test_console_channel_ui.py tests/test_console_pro_ui.py

- [ ] **Step 3: 实现 console-channel.jsx。**

全局导出：

    WwChannelStatusBadge
    WwChannelActionCard
    WwSecureTokenCard
    WwChannelDrawer
    WwDeliveryRow
    WwChannelConfirmDetails
    wwChannelViewState

Token 流：

    Channel status says awaiting_token
      -> WwSecureTokenCard local password state
      -> request one-time action ticket
      -> POST /console/channels/telegram/token
      -> clear local value in finally
      -> refresh redacted status

配对请求、连接成功、重试、uncertain、凭证失效均由 status API 真值驱动，不从 ToolChain 或按钮猜测。

WeiwoApp 唯一的用户文本 dispatch（普通 Composer 与 ConfirmModal 转向框共用）在调用任何 onSend/WW.send/WW.steer 前先检查 wwLooksLikeTelegramToken：当前有 draft 时申请绑定 sid + `telegram:submit-token` + draft_id 的 ticket 并走安全 token endpoint；无 draft 时清空本地秘密值并显示“先让 Agent 创建连接草稿”。两种情况都不得调用 WW.send、WW.steer 或 optimistic message append。服务端 /send 与 /steer 截流仍是最终边界，并拒绝没有有效 ticket 的秘密提交。

- [ ] **Step 4: 实现 console-data.jsx API。**

增加并导出：

    wwChannelStatus(sid)
    wwChannelRequestToken(sid, action, subjectId)
    wwTelegramSubmitToken(sid, draftId, token, requestToken)
    wwChannelManage(sid, action, requestToken)
    wwChannelResolveDelivery(sid, itemId, action, requestToken)
    wwChannelConfirm(sid, turnId, toolName, choice)
    wwLooksLikeTelegramToken(text)

draftId 只取当前 status.draft.draft_id，不从 ToolChain 文本或 DOM 猜测。客户端 Token 检测只改善体验，服务端仍是边界。错误只展示安全 reason/error_code，不 stringify 原始异常或 request。

wwChannelManage 仅允许 pause、resume、reverify、replace-token、disconnect；不得把任意 action 拼 URL。sid、draftId、itemId 均 encodeURIComponent。replace-token 动作只让后端创建新的 awaiting-token 状态并打开安全卡，Token 仍只走 wwTelegramSubmitToken。

- [ ] **Step 5: 接入 WeiwoApp、标题栏和左栏。**

WeiwoApp 增加 channelBySid、channelDrawerOpen、channelRequestSeqRef。每次状态请求先递增 sequence，只有同时满足 requestIsCurrent(sid, generation) 且 sequence 等于当前最新值才提交状态。8 秒轮询读取 streamSidRef.current/streamGenRef.current，不捕获首渲染 state.sid；awaiting_pairing/pairing_pending 的 2 秒 effect 在状态改变、切会话和卸载时清理。attach 后立即拉；相关 tool_result 和管理动作成功后重拉。

WwSessBar 显示完整徽章；左栏 sessionRow 只显示小状态点，不挤 Bot 名称。WwChannelActionCard 放在消息区与输入坞之间，不进入 items.map。

status view_state 按 Task 4 的稳定契约渲染，不重新猜领域状态。recent_deliveries 只用 outbox_id/kind/state/media_id/time/attempts/error_class，并按 media_id 合并到对应消息 item.delivery；不接收或 stringify credential_id、正文、caption、path。

- [ ] **Step 6: 实现专用确认和投递状态。**

ConfirmModal 和 WwMediaImageCard 保留现签名。Telegram/automation 工具只把 public_details 传给 WwChannelConfirmDetails，并使用后端 title/approve_label；该分支 return 后不得再渲染通用 JSON。专用确认的肯定按钮调用 wwChannelConfirm：先按 action=`confirm:<tool_name>`、subjectId=turn_id 申请 ticket，再把 ticket 随现有 /console/confirm body 提交；finally 清掉局部引用，不存浏览器存储。重复点击由按钮 in-flight 锁和服务端单次消费共同阻止；过期时可重新申请，跨 sid/tool/turn 的票据必定失败。拒绝、停止、转向和非通道工具继续走现有行为。媒体卡只从 item.delivery 增加服务端 delivery 状态标签，不增加随手发送按钮。uncertain 提供：

- 视为已送达并继续。
- 重新发送并接受可能重复。
- 放弃本条并继续。

terminal 提供“修复后重试”和“放弃本条并继续”。所有人工决议均二次确认并在 2xx 后重拉；这样 disconnect 后的 uncertain 不会与 hard-delete blocker 形成死锁。

disconnect 同样二次确认。Bot ID 在 replace-token 后改变时显示 needs_reconnect/需重新配对，绝不沿用 active。归档会话的 drawer 只读，不能 pause/resume/reverify/replace/disconnect；用户需先恢复会话，再显式处理通道。

- [ ] **Step 7: 实现隔离样式。**

console-channel.css 只用 ww-channel-*；复用 --paper、--ink、--yin、--dai、--jin；支持 420px 抽屉和 1440/1120/760 三档，不覆盖既有消息、媒体卡和抽屉类。抽屉沿用 top:44px，z-index 高于窄屏 Workspace Dock 的 52。

attach 切会话、openStage 进入全宽工作台、研究空间切换清理 Stage、当前会话归档并自动切换时都关闭 channel drawer；打开通道抽屉关闭研报抽屉，反向亦然。

观澜 · 帷幄.html 必须在 console-data.jsx 之后、console-thread.jsx 之前加载 console-channel.jsx。

- [ ] **Step 8: 跑绿测和精确提交。**

    python -m pytest -q tests/test_console_channel_ui.py tests/test_console_pro_ui.py tests/test_console_media.py
    git add -- ui/console/console-channel.jsx ui/console/console-channel.css ui/console/console-data.jsx ui/console/console-app.jsx ui/console/console-thread.jsx ui/console/console-rail.jsx "ui/console/观澜 · 帷幄.html" tests/test_console_channel_ui.py tests/test_console_pro_ui.py
    git commit -m "feat(console): add secure Telegram channel management UI"

---

## Task 10: 会话级定时任务、Prompt/Skill 白名单和安全系统轮次

**Files:**

- Create: guanlan_v2/channels/automation.py
- Create: guanlan_v2/channels/automation_skills.py
- Create: guanlan_v2/channels/trading_calendar.py
- Modify: guanlan_v2/channels/runtime.py
- Modify: guanlan_v2/channels/store.py
- Modify: guanlan_v2/channels/agent_tools.py
- Modify: guanlan_v2/console/api.py
- Modify: ui/console/console-channel.jsx
- Test: tests/test_channels_automation.py
- Modify: tests/test_console_channels.py
- Modify: tests/test_console_channel_ui.py

**V1 schedules:**

    once:         run_at = aware ISO datetime
    daily:        local_time = HH:MM
    weekdays:     local_time = HH:MM
    trading_days: local_time = HH:MM + injected real A-share calendar

- [ ] **Step 1: 写日程计算红测。**

覆盖 ZoneInfo Asia/Hong_Kong、DST 时区、once、daily、weekdays、真实 trading-day 注入、无交易日历时诚实 blocked、next_run_at、相同 occurrence 幂等、最新一次漏跑、6 小时 grace、超期 missed。另测两个 scheduler 只有一个 claim；queued lease 过期可回收；running 重启后按 Console 证据收敛而不盲目重跑。

- [ ] **Step 2: 写运行语义红测。**

到点先写 automation_run queued；会话忙则保持 queued，不打断；空闲后只通过 Console submit_system_turn hook 启动。内部 automation_trigger 标 channel_projection=never；Agent final 正常进入 Console 后才由 Projector 镜像。没有隐藏 Telegram 直发路径。

- [ ] **Step 3: 写自动化权限红测。**

自动化轮次默认拒绝所有 confirm_required 工具。confirm_cb 必须先处理 automation 分支，再评估现有 workspace_auto；因此自动化不会借空间自主模式放行其他写工具。只有 media_policy=explicit、且 media_id 从同一 turn 的可信 media_image artifact 收集并再次 load_for_session 复验时，ww_telegram_publish 可使用已确认任务授权自动放行。不得发送旧图、别的会话图或任意路径。

- [ ] **Step 4: 运行红测。**

    python -m pytest -q tests/test_channels_automation.py tests/test_console_channels.py

- [ ] **Step 5: 实现结构化 ScheduleCalculator 和严格 A 股日历 provider。**

只用 datetime、zoneinfo 和注入 clock/calendar；不把 Monday–Friday 冒充 A 股交易日。trading_calendar.py 复用仓内现有权威来源 `financial_analyst.data.loader_factory.get_default_loader()._load_calendar("day")`，惰性加载并规范化为日期集合；加载失败抛安全 CalendarUnavailable，不采用 watcher 的工作日兜底。每次 upsert 必须验证 name、schedule、timezone、prompt_text、skill_refs、media_policy，并保存 confirmed_at。

- [ ] **Step 6: 实现白名单 Skill resolver。**

automation_skills.py 只注册仓内稳定 ID，例如：

    morning_brief_v1
    closing_review_v1

每项是固定流程文本、数据优先级、引用和失败降级规则。skill_refs 只能引用注册 ID，不接受路径、URL 或任意文件。任意分析要求仍可用已确认 prompt_text 表达；自定义 Skill 创作不在本 phase。

- [ ] **Step 7: 给 Console 暴露同 loop hook。**

build_console_router 在所有闭包定义完成后调用：

    channel_runtime.bind_console_hooks(
        emit=_emit,
        is_busy=lambda sid: sid in _busy_session_ids(),
        submit_system_turn=_submit_system_turn,
        on_turn_terminal=_on_turn_terminal,
    )

_submit_system_turn 验证会话/研究空间未归档、会话空闲，落 automation_trigger 后调用唯一 _launch_turn，并返回稳定 turn_id 或 busy。control 增加 source、automation_run_id、automation_media_policy、created_media_ids。每个 tool_result 若来自可信 media_image artifact，则把 media_id 加进 control.created_media_ids；confirm_cb 的 automation 分支优先于 workspace_auto。不得 HTTP loopback。

_run_turn 在 agent_final（若有）和 task_update done 都完成后，唯一调用 _on_turn_terminal(automation_run_id, turn_id, ok, final_event_id, error_class)。回调幂等更新 automation_runs；没有 automation_run_id 的普通轮次 no-op。

- [ ] **Step 8: 实现 automation worker。**

每 15 秒：

1. materialize due/misfire occurrences，依赖数据库唯一键防重。
2. 以 lease_owner/lease_until 原子 claim queued；会话空闲时提交系统轮次。
3. turn 完成回调更新 done/error 和 final_event_id。
4. 会话忙继续排队；归档暂停。

automation scheduler 自身使用 60 秒 lease、20 秒续约。启动恢复时：queued/过期 claim 回到 queued；running 若 Console 已有同 turn 的 agent_final/done 则收敛 done，否则标 error=service_restart_interrupted，绝不自动重跑可能已发生副作用的轮次。runtime.stop 释放 scheduler lease。

系统轮次文本清晰分隔受信 schedule 元数据、已确认 Prompt 和白名单 Skill block，不含 Bot Token、Chat ID 或本地路径。

- [ ] **Step 9: 完成 Agent 工具和抽屉日程区。**

ww_automation_upsert 必须在确认卡完整复述时间、时区、内容、Skill、媒体策略。list/pause/resume/delete 只限当前 sid。抽屉展示任务状态、next/last run 和暂停原因，不允许跨会话移动。

- [ ] **Step 10: 跑绿测和精确提交。**

    python -m pytest -q tests/test_channels_automation.py tests/test_console_channels.py tests/test_console_channel_ui.py tests/test_console_api.py
    git add -- guanlan_v2/channels/automation.py guanlan_v2/channels/automation_skills.py guanlan_v2/channels/trading_calendar.py guanlan_v2/channels/runtime.py guanlan_v2/channels/store.py guanlan_v2/channels/agent_tools.py guanlan_v2/console/api.py ui/console/console-channel.jsx tests/test_channels_automation.py tests/test_console_channels.py tests/test_console_channel_ui.py
    git commit -m "feat(channels): run confirmed automations inside bound conversations"

---

## Task 11: 会话生命周期、服务 lifespan、恢复与可观测性

**Files:**

- Modify: guanlan_v2/server.py
- Modify: guanlan_v2/console/api.py
- Modify: guanlan_v2/channels/runtime.py
- Modify: guanlan_v2/channels/store.py
- Modify: guanlan_v2/channels/api.py
- Modify: .env.example
- Test: tests/test_channels_lifecycle.py
- Modify: tests/test_console_api.py
- Modify: tests/test_server_script_launch.py
- Modify: tests/desktop/test_server_script_launch.py
- Modify: tests/desktop/test_main_entrypoint.py

- [ ] **Step 1: 写归档/恢复/删除红测。**

冻结行为：

- 归档顺序是先暂停 binding 和 automations、取消 pending/retry Outbox，再 set_archived。
- Channel 暂停失败时不归档；Console 归档随后失败时保持已暂停，宁可少发不能多发。
- restore 只恢复会话，Telegram/automation 仍 paused。
- hard delete 发现未 disconnected binding、未解决 Outbox（pending/sending/retry_wait/uncertain/terminal）或未删除 automation 时返回 409，要求先处理；不做跨 JSON/SQLite 伪原子清理。
- hard delete 还阻止 draft_session_id 指向该 sid 的 draft/awaiting_pairing/poll_conflict Bot，以及 credential_cleanup_queue 中未清理的凭证。
- 工作空间归档对其会话执行同样的安全暂停。

并发测试覆盖 projector/outbox claim 与 archive 同时发生：intentional pause 的 BEGIN IMMEDIATE 必须先冻结新 claim，再取消/转换队列。workspace 多会话暂停使用单个 Channel SQLite 事务；事务失败全部回滚并拒绝归档，Console workspace archive 随后失败则返回 safe_paused_sids，保持全部已暂停。

- [ ] **Step 2: 写重启恢复红测。**

覆盖：Bot/绑定永久保留；sending -> uncertain；pending/retry 恢复；active Bot 重新 getMe；同 Bot ID 保留绑定；Bot ID 改变立即 fault-pause 旧绑定、停止 claim 并进入 needs_reconnect，只有新私聊配对批准后才恢复；网络失败不删除绑定；poll lease 恢复；归档状态不自动 resume。poll_conflict 显形为 needs_reconnect + error_code=poll_conflict，lease 到期后可 reverify 重新竞争，重复 409 仍保持故障暂停且不丢队列。已 disconnected binding 上残留 uncertain/terminal 仍通过 status DTO 可见并可决议。另测 credential_cleanup_queue 重试、被数据库引用的密文绝不删除、新建未引用密文在一小时 grace 内不删除、一小时后孤儿密文才删除，以及 stop 会关闭 Runtime 自有 HTTP client。

- [ ] **Step 3: 写 feature gate 与 lifespan 红测。**

GUANLAN_TELEGRAM_ENABLED 未设时不创建网络 task，现有 Console 行为不变；设为 1 时 runtime.start 在 app lifespan 内调用一次，stop 在 finally await 一次。不得启动 import-time daemon。

- [ ] **Step 4: 运行红测。**

    python -m pytest -q tests/test_channels_lifecycle.py tests/test_console_api.py tests/test_server_script_launch.py tests/desktop/test_server_script_launch.py tests/desktop/test_main_entrypoint.py

- [ ] **Step 5: 装配 server.py。**

只在 GUANLAN_TELEGRAM_ENABLED=1 时，于唯一 ConsoleStore 创建后构造 ChannelStore、SecretStore、MediaStore、TelegramAdapter 和 ChannelRuntime；include build_channel_router(runtime)，并将 runtime 注入 build_console_router。feature gate 关闭时传 channel_runtime=None，不实例化 DPAPI/HTTP/SQLite 通道组件，也不创建网络 task；前端把 404 归一为 available=false。router 构造完成后 runtime 已绑定 Console hooks。

生产 trading_days provider 明确注入 Task 10 的 `guanlan_v2.channels.trading_calendar.AShareTradingCalendar`；它读取仓内引擎 day 日历。导入或日历加载失败时只把 trading_days 自动化标 blocked，不用 weekdays 近似，不阻断其他 schedule。

runtime.start 在启动任何 projector/outbox/automation worker 前先扫描 Console session/workspace archive 状态；若 feature gate 曾关闭期间发生归档，原子 intentional-pause 对应 binding/automation 并取消待发，保证重新开启不会补发归档期内容。随后以 ChannelStore 当前 credential 引用集合对账 SecretStore：先重试 credential_cleanup_queue，只删除合法命名、未被引用且 mtime 超过一小时 grace 的孤儿密文；不得读取明文做对账。运行期低频重复同一对账，runtime.stop 取消并 await 清理 task，最后关闭由 Runtime 创建的 httpx client。

在现有 composed lifespan 的 inner scope 中：

    if telegram_enabled:
        await channel_runtime.start()
    try:
        yield
    finally:
        if telegram_enabled:
            await channel_runtime.stop()

将 app.state.channel_runtime 暴露给测试和本机健康检查，不暴露秘密。

- [ ] **Step 6: 实现会话生命周期接线。**

sessions_archive、workspace archive、restore、sessions_delete 分别调用 runtime 的安全领域方法。所有调用在 Console 落盘前完成并返回结构化 blocker/reason_code。

- [ ] **Step 7: 实现状态与健康摘要。**

GET status 和 sessions channel_summary 只暴露：状态、Bot display_name/username、接收者安全元数据、pending/retry/uncertain/terminal 数量、最后成功时间、最后 error_class、自动化数量。审计不含消息全文。

- [ ] **Step 8: 更新 .env.example。**

    GUANLAN_TELEGRAM_ENABLED=0

其他退避、轮询参数保持代码安全默认，不把 Token 放进环境示例。

- [ ] **Step 9: 跑绿测和精确提交。**

    python -m pytest -q tests/test_channels_lifecycle.py tests/test_console_api.py tests/test_server_script_launch.py tests/desktop/test_server_script_launch.py tests/desktop/test_main_entrypoint.py
    git add -- guanlan_v2/server.py guanlan_v2/console/api.py guanlan_v2/channels/runtime.py guanlan_v2/channels/store.py guanlan_v2/channels/api.py .env.example tests/test_channels_lifecycle.py tests/test_console_api.py tests/test_server_script_launch.py tests/desktop/test_server_script_launch.py tests/desktop/test_main_entrypoint.py
    git commit -m "feat(channels): wire Telegram runtime into console lifecycle"

---

## Task 12: 假 Telegram 端到端、实机验收、文档与发布闸

**Files:**

- Create: tests/test_telegram_readonly_integration.py
- Modify: ui/console/README.md
- Create: docs/operations/telegram-readonly-channel.md
- Modify: docs/superpowers/specs/2026-08-22-telegram-readonly-channel-design.md

- [ ] **Step 1: 写完整 fake Telegram E2E。**

同一 FastAPI lifespan + 临时 ConsoleStore/ChannelStore + MockTransport 验证：

1. Agent 创建 draft。
2. 安全卡提交 Token，raw token 全盘扫描为零。
3. getMe 校验。
4. 私聊产生配对码，入站不进 Agent。
5. Agent 提交码并用 sid + tool + turn 绑定的单次票据确认；重放、过期和跨会话票据均失败，成功后形成严格一对一 binding。
6. 批准前历史不发。
7. 批准后的电脑 user_msg 和 agent_final 按序 sendMessage。
8. delta、工具、进度、确认、错误不发。
9. 显式 media_id 经 sendPhoto。
10. 普通 Telegram 入站继续被丢弃。
11. 429、401、403、5xx、超时和 uncertain 状态正确。
12. 重启后游标、Outbox、绑定、自动化恢复。

- [ ] **Step 2: 写安全表面扫描。**

真实测试 Token 在 events、buddy_state、SQLite 文本列、Outbox、API JSON、caplog 和普通文件中出现次数必须为 0；密钥目录只允许 DPAPI 密文，不允许 plaintext。由于已确认产品流程是“用户把一小时配对码告诉 Agent”，该码只允许出现在原始 Console user_msg、Buddy 用户消息和 Buddy tool-call protocol；Channel SQLite、confirm_request、channel_audit、Outbox、API DTO、日志和前端 status state 中出现次数必须为 0。本地媒体路径不得进入 Channel SQLite、API DTO、Telegram request 或日志。

- [ ] **Step 3: 跑分层回归。**

    python -m pytest -q tests/test_channels_secret_store.py tests/test_channels_store.py
    python -m pytest -q tests/test_channels_telegram.py tests/test_channels_pairing.py tests/test_channels_api.py
    python -m pytest -q tests/test_channels_projector.py tests/test_channels_outbox.py tests/test_channels_media.py
    python -m pytest -q tests/test_channels_automation.py tests/test_channels_lifecycle.py tests/test_telegram_readonly_integration.py
    python -m pytest -q tests/test_console_api.py tests/test_console_store.py tests/test_console_tools.py tests/test_console_media.py tests/test_console_channel_ui.py tests/test_console_pro_ui.py
    python -m pytest -q tests/test_agent_interface_doc.py tests/test_server_script_launch.py tests/desktop/test_server_script_launch.py tests/desktop/test_main_entrypoint.py

- [ ] **Step 4: 跑全仓测试。**

    python -m pytest -q

如全仓存在已知无关失败，必须记录完整命令、失败测试和与本变更无关的证据；不得把 targeted green 冒充全绿。

- [ ] **Step 5: 更新运维与用户文档。**

docs/operations/telegram-readonly-channel.md 说明：

- 开启 feature gate。
- Agent 主导连接、Token 安全卡、私聊取码、电脑端确认。
- 配对码一小时，绑定永久。
- Telegram 只读，不能命令 Agent。
- 文字/图片/定时任务边界。
- pause/resume/disconnect/replace token。
- retry/uncertain/terminal 的用户处理。
- 备份 channels.sqlite3 和 DPAPI 账户迁移限制。
- 不把 Token 放到 env、Prompt、日志或截图。

ui/console/README.md 补非技术操作入口和状态文案。

- [ ] **Step 6: 本机无秘密 UI 验收。**

启动 feature gate 后，用浏览器/电脑控制验证 1440、1120、760 三档；切换两个会话确认状态不串；抽屉互斥；归档/恢复保持暂停。Token 仍在输入框时禁止截图、DOM snapshot 和浏览器日志采集；提交并清空后才做视觉检查。

- [ ] **Step 7: 请求用户提供真实 Bot 名称和 Token。**

只在 fake E2E、全盘泄漏测试和 UI 安全卡均通过后请求。Token 只能粘到安全卡，不得粘到实施聊天、测试代码或 shell。随后：

1. getMe 显示真实 Bot username。
2. 用户私聊 Bot，获得一小时码。
3. Agent 展示 Telegram 用户 ID/username 和永久一对一确认。
4. 用户确认。
5. 验证标题 已连接 Telegram · Bot名称。
6. 发送一条用户文字和一条 Agent final。
7. 显式发送一张测试图。
8. 从 Telegram 发普通消息，确认 Agent/Console 零反应。
9. 重启服务，确认绑定仍在。

- [ ] **Step 8: 把设计状态改为已实施并记录验收证据。**

只有全部自动测试和真实 Bot 正常路径通过后，才将设计文档状态改为 已实施并实机验收。不得在用户尚未提供凭证时提前宣称完成。

- [ ] **Step 9: 精确提交。**

    git add -- tests/test_telegram_readonly_integration.py ui/console/README.md docs/operations/telegram-readonly-channel.md docs/superpowers/specs/2026-08-22-telegram-readonly-channel-design.md
    git commit -m "test(channels): prove Telegram read-only flow end to end"

---

## Review checkpoints

- [ ] **Checkpoint A — Task 3 后：** 审查 agent_final 是否只在完整 done 后出现；取消、转向、工具前文字和重启切口是否安全。
- [ ] **Checkpoint B — Task 5 后：** 审查 Token、配对码、入站丢弃、SQLite 双向唯一约束和 Bot 身份绑定。
- [ ] **Checkpoint C — Task 7 后：** 审查 cursor + Outbox 原子性、超过 2,000 事件、顺序、uncertain 和重启恢复。
- [ ] **Checkpoint D — Task 8 后：** 审查 media_id ownership 和普通图片不自动外发。
- [ ] **Checkpoint E — Task 10 后：** 审查自动化只进同一 Console turn、busy 排队、真实交易日历和媒体预授权范围。
- [ ] **Checkpoint F — Task 12 后：** 审查全盘秘密扫描、现有 Console 回归和真实 Bot 实机证据。

## Exit gates

- [ ] 未绑定/feature gate 关闭时，现有 Console/SSE/转向/落子/研究空间/归档行为不变。
- [ ] Token 不存在于 Prompt、事件、Buddy 状态、Outbox、SQLite 明文字段、API DTO、异常、日志、测试快照和截图。
- [ ] 一个 session、Bot、Telegram 私聊接收者的双向唯一约束由 SQLite 原子保证。
- [ ] Telegram 入站在 active/paused 状态只推进 offset，永不触发 Agent、Skill、MCP、文件或落子。
- [ ] 只有 user_msg 和 agent_final 自动投影；显式且已确认的 telegram_media_publish 只走 Task 8 严格分支，其余中间态、工具结果、通知占位和系统控制事件均黑名单。
- [ ] 绑定前历史、暂停期历史、归档期历史不补发。
- [ ] Outbox 按 binding 严格有序；uncertain 不盲重试；用户可明确决议后继续。
- [ ] 媒体只来自当前 sid 的 MediaStore bytes；Outbox 无路径。
- [ ] 自动化先进入帷幄，再投影 Telegram；忙时排队，不打断。
- [ ] 归档自动暂停，恢复不自动恢复；hard delete 有 blocker。
- [ ] fake Telegram E2E、分层回归、全仓测试和真实 Bot 正常路径均有证据。

## Execution handoff

推荐使用 Subagent-Driven 方式逐 Task 实施：每个 Task 由独立实现者按红绿重构完成，再由另一名审查者按本计划和 authoritative design 复核。主 Agent 只合并通过 checkpoint 的提交，并始终使用显式 pathspec，避免碰到当前工作树中的基金流向和 wisdom 运行时改动。
