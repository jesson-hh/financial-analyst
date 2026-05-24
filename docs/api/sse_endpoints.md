# Buddy SSE Bridge — HTTP / SSE API Reference

`financial-analyst serve --port 9999` 起一个 FastAPI 服务, 给 GuanLan UI (或任何
HTTP 客户端) 调真后端用. 默认 `http://127.0.0.1:9999`, 开发期 CORS 全开.

> 本文档与 `src/financial_analyst/buddy/server.py` 对齐 (v1.9.4+). 接口变动同步
> 更新此文档.

## 启停

```bash
# 启动
financial-analyst serve --port 9999

# 健康检查
curl http://127.0.0.1:9999/health
# → {"ok": true, "version": "1.9.4", "tools": 22}
```

需要 `fastapi + uvicorn`. 没装会报错引导到 `pip install financial-analyst[serve]`.

## Endpoint 概览

| Method | Path | 用途 | 典型耗时 |
|--------|------|------|---------|
| GET | `/health` | 版本 + tool 数 | <10ms |
| GET | `/tools` | 可用工具列表 | <10ms |
| GET | `/models` | 可用 LLM 模型 | <50ms |
| POST | `/run` | **SSE 主入口** — 一轮对话 | 流式, 视 query 复杂度 |
| POST | `/confirm` | 回复 confirm_request 的 y/n/a | <10ms |
| POST | `/compact` | 压缩会话历史成摘要 | ~10s (LLM) |
| GET | `/quotes?codes=...` | 批量实时行情 (腾讯) | ~120ms |
| GET | `/resolve?q=...` | 代码/名称解析成 `{code, name}` | <500ms |
| GET | `/comments?code=...` | 雪球评论 + 情绪 | <500ms 本地 / 2-15s 现拉 |
| GET | `/xueqiu/hot` | 雪球热股榜 (TTL 5min) | ~200ms 现拉, <10ms 缓存 |
| GET | `/xueqiu/feed` | 雪球关注时间线 (TTL 5min) | 同上 |
| GET | `/diag?quick=0/1` | **API 稳定性深度探活** | ~2s quick / ~20s full |
| GET | `/report-progress?code=X` | 轮询 deep-report 进度 | <10ms |
| POST | `/lesson {text}` | 沉淀对话经验 | <50ms |
| GET | `/report?path=...` | 读取已生成的研报 .md | <50ms |
| POST | `/conversations` | 保存会话 | <50ms |
| GET | `/conversations` | 列出已保存会话 | <50ms |
| GET | `/conversations/{cid}` | 读取一个会话 | <50ms |
| DELETE | `/conversations/{cid}` | **软删** (移到 _trash/, 30 天自动 purge). `?permanent=1` 立刻硬删 | <50ms |
| GET | `/conversations/trash` | 列出回收站会话 (含 deletedAt) | <50ms |
| POST | `/conversations/{cid}/restore` | 从回收站恢复 (body 可选 `{trash_filename}` 指定副本) | <50ms |
| GET | `/alerts` | 列出价格预警规则 | <50ms |
| DELETE | `/alerts/{rule_id}` | 删一条盯盘规则 (UI ❌ 按钮) | <50ms |
| GET | `/alerts/check` | 评估一次预警 (开盘内才评估) | ~500ms |

---

## POST `/run` — SSE 主入口

会话的核心. 走 [Server-Sent Events](https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events), 一次 HTTP 长连接里推送多个 event.

### 请求体

```json
{
  "query": "看下茅台怎么样",
  "mode": "default",
  "session_id": "abc123",
  "model": null,
  "context": null
}
```

| 字段 | 类型 | 必需 | 说明 |
|------|------|------|------|
| `query` | string | ✓ | 用户输入 |
| `mode` | string | | `default` (Tier-1+ 工具需确认) / `safe` (全部确认) / `auto` (全自动) |
| `session_id` | string | | 多轮上下文 key. 同 id 下 24 条 LRU 复用 BuddyAgent. 不传 = stateless |
| `model` | string | | 临时切模型 (如 `qwen3-max`). 仅本轮有效, 不持久化 |
| `context` | object | | 预留 (目前未用) |

### 响应 SSE Event 顺序

```
event: plan
data: {"turn_id": "f1a2…", "intent": "deep_research", "label": "深度研报"}

event: tool_start
data: {"idx": 0, "name": "stock_brief", "args": {"code": "SH600519"}}

event: tool_done
data: {"idx": 0, "result": "{ok: true, ...}", "is_error": false}

event: brief
data: {"sym": {"code": "SH600519", "name": "贵州茅台", "price": 1332.95, ...}}

event: confirm_request
data: {"turn_id": "f1a2…", "tool": "run_report", "args": {"code": "SH600519"}}
        ↑ 阻塞中, 客户端必须 POST /confirm 才能继续

event: tool_start
data: {"idx": 1, "name": "run_report", "args": {...}}

event: report
data: {"path": "G:\\financial-analyst\\out\\SH600519_2026-05-23.md"}

event: tool_done
data: {"idx": 1, "result": "...", "is_error": false}

event: answer_progress
data: {"text": "贵州茅台目前 1332.95 元..."}

event: done
data: {"tokens": 12450}
```

### SSE Event 字典

| Event | 字段 | 触发条件 |
|-------|------|---------|
| `plan` | `turn_id`, `intent`, `label` | 每轮开头 1 次. `intent` ∈ {quick_quote, deep_research, ask, chat, …}, `label` 是中文展示用 |
| `tool_start` | `idx`, `name`, `args` | 每次 LLM 调工具 |
| `tool_done` | `idx`, `result` (前 240 字), `is_error` | 工具返回 |
| `brief` | `sym` (完整 brief 卡数据) | 工具结果含 `side_effect.brief` 时附带 |
| `report` | `path` (深度研报 .md 路径) | `run_report` 跑完时附带 |
| `answer_progress` | `text` (累积 LLM 答复) | LLM 流式生成文本时 |
| `confirm_request` | `turn_id`, `tool`, `args` | 需要 y/n/a (按 `mode` 触发) |
| `done` | `tokens` (本轮总 token 数, 可能为 null) | 每轮结尾, 一定有 |
| `error` | `message` (`类型: 消息`) | 异常时. `done` 仍会发 |

### 错误处理

- 任何工具失败 → `tool_done.is_error=true`, **不中断**, LLM 会看到错误并尝试其他方式
- LLM 调用失败 / 超时 → `error` event + `done`
- `confirm_request` 后客户端 30s 内未 POST /confirm → 默认 `n` (拒绝)

### 客户端最小实现 (JS)

```javascript
const res = await fetch('http://127.0.0.1:9999/run', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({query: '看下茅台', session_id: 'abc'}),
});
const reader = res.body.getReader();
const dec = new TextDecoder();
let buf = '';
while (true) {
  const {value, done} = await reader.read();
  if (done) break;
  buf += dec.decode(value, {stream: true});
  // 解析 "event: NAME\ndata: JSON\n\n" 帧, 见 GuanLan agent-adapter.jsx
}
```

完整实现参考 `G:/stocks/fa_ui_ready/agent-adapter.jsx`.

---

## POST `/confirm` — 应答确认

```json
// req
{"turn_id": "f1a2…", "choice": "y"}    // y/a 通过, n 拒绝

// resp
{"ok": true}
// 或 404 {"ok": false, "reason": "no pending confirm"}
```

`choice` ∈ `y/yes/a/always` 视为通过, 其他视为拒绝.

---

## POST `/compact` — 压缩历史

把多轮会话压缩成几句摘要, 释放上下文.

```json
// req
{
  "session_id": "abc",            // optional, 优先用内存里的 history
  "transcript": "user: ...\nass: ..."   // 后备, 服务端重启丢了内存 history 时用
}

// resp
{"ok": true, "summary": "用户在追问茅台基本面..."}
```

---

## GET `/quotes?codes=...` — 批量实时行情

走腾讯免 cookie 接口. 几十只股票 ~120ms.

```bash
curl 'http://127.0.0.1:9999/quotes?codes=SH600519,SZ300750,002594'
```

```json
{
  "ok": true,
  "quotes": [
    {"code": "SH600519", "name": "贵州茅台", "price": 1332.95,
     "changePercent": -0.42, "turnover": 28.4e8, ...},
    ...
  ]
}
```

代码可带/不带前缀, 也接受 6 位 (自动猜 SH/SZ).

---

## GET `/comments?code=X&refresh=0/1&limit=8&sentiment=0/1` — 雪球评论 + 情绪

两段式设计避免阻塞 UI:

| 调用 | 用途 | 耗时 |
|------|------|------|
| `?refresh=0&sentiment=0` | 本地 NewsDB 秒出 | <50ms |
| `?refresh=1&sentiment=0` | 现拉雪球 (xueqiu_comments) + upsert 入库, 不算情绪 | 500-2000ms |
| `?refresh=0&sentiment=1` | 本地评论 + LLM 情绪聚合 (~10-20s qwen3.5-plus) | 慢, 后台拉 |

```json
{
  "ok": true,
  "comments": [
    {"author": "...", "text": "...", "time": "2026-05-22 14:30", "url": "..."},
    ...
  ],
  "sentiment": {            // 仅 sentiment=1 时
    "bull": 35, "bear": 25, "neutral": 40,
    "summary": "散户分歧明显, 看多看空均有立论"
  },
  "error": null
}
```

> ⚠ 历史踩坑: `_safe_json_dumps` 会把 NaN/Inf 替换成 None. 浏览器 `JSON.parse('NaN')`
> 会抛 SyntaxError 静默吞掉整个 SSE event. 详见 `data/net.py` + pitfall #0.

---

## GET `/diag?quick=0/1` — API 稳定性深度探活

并行探 5 源 + LLM, 返回每源 ok/latency/detail + 限速层累计统计.

| `quick=0` (默认) | `quick=1` |
|------|------|
| 全探, 含 LLM | 跳过 LLM (用于秒级看面板) |
| ~20s (LLM ~15s) | ~2s |

```json
{
  "ok": true,                     // 全 ok 才 true
  "results": [
    {"name": "xueqiu_comments", "ok": true, "latency_ms": 234, "detail": "3 comments"},
    {"name": "xueqiu_hot_stock", "ok": true, "latency_ms": 187, "detail": "5 hot stocks, top='贵州茅台'"},
    {"name": "tencent_quote",    "ok": true, "latency_ms": 142, "detail": "price=1332.95 pct=-0.42"},
    {"name": "news_db",          "ok": true, "latency_ms": 8, "detail": "1 recent news in db"},
    {"name": "llm",              "ok": true, "latency_ms": 14210, "detail": "model=qwen3.5-plus reply='OK'"}
  ],
  "rate_limit_stats": {           // data/net.py 累计统计
    "xueqiu":         {"calls": 12, "retries": 0, "throttled": 0, "cache_hits": 8, "last_error": null},
    "xueqiu_hot":     {"calls": 3,  "retries": 0, "throttled": 0, "cache_hits": 2, "last_error": null},
    "tencent_quote":  {"calls": 47, "retries": 1, "throttled": 0, "cache_hits": 30, "last_error": null},
    "tushare":        {"calls": 0, ...},
    "eastmoney_kuaixun": ...,
    "ths_hot": ...
  }
}
```

前端 🩺 探活按钮调这个. 异常时哪源红哪源绿一目了然.

---

## GET `/report-progress?code=X` — 深度研报进度

前端 deep-report 抽屉每 1-2s 轮询, 出 14 agent 实时状态.

```bash
curl 'http://127.0.0.1:9999/report-progress?code=SH600519'
```

```json
{
  "ok": true,
  "code": "SH600519",
  "asof": "2026-05-23",
  "ts": 1779534878.42,
  "total": 14,
  "done": 14,
  "fail": 0,
  "running": 0,
  "pending": 0,
  "agents": {
    "quote-fetcher": {"state": "done", "elapsed": 0.016},
    "factor-computer": {"state": "done", "elapsed": 0.36},
    "...": {...},
    "report-writer": {"state": "done", "elapsed": 137.19},
    "introspector": {"state": "done", "elapsed": 86.90}
  }
}
```

状态: `pending` / `running` / `done` / `fail`. `elapsed` 单位秒.

未跑过 → `{"ok": false, "error": "not_started", ...}`.

---

## POST `/lesson` — 沉淀对话经验

UI 里 `/lesson <text>` slash command 调这个. 追加到
`memories/_shared/conversation_lessons.md`. **下次 buddy 启动**自动 prepend 到
`SYSTEM_PROMPT`. (`buddy/agent.py::_load_conversation_lessons` 每次 build prompt
时读文件 → 不需要重启 backend.)

```json
// req
{"text": "用户偏好: 点自选股只看不要 prefill 到 composer"}

// resp
{"ok": true, "appended": "用户偏好: ...", "note": "下次重启 buddy 后端后生效"}
```

> note 文案 legacy, 实际 hot-reload, 立即生效. (P2 修.)

---

## GET `/resolve?q=...` — 代码/名称解析

UI 自选股批量加用. 接受 6 位代码 / SH/SZ/BJ 前缀 / 名称.

```json
{"ok": true, "code": "SH600519", "name": "贵州茅台"}
// 或
{"ok": false, "q": "..."}
```

---

## GET `/report?path=...` — 读研报正文

读 `out/<CODE>_<DATE>.md` 全文. **限制只能读 `out/` 目录下**.

```json
{"ok": true, "text": "# 贵州茅台 (SH600519) — Deep-Dive Research\n..."}
```

---

## /conversations CRUD + 回收站 — 多会话磁盘存储

浏览器缓存清了也不丢. 存 `~/.financial-analyst/conversations/{cid}.json`.
**v1.9.5 起 DELETE 默认软删** (move to `_trash/` 子目录), 30 天自动 purge, 支持
restore.

```bash
# 保存
curl -X POST http://127.0.0.1:9999/conversations \
  -H 'Content-Type: application/json' \
  -d '{"id": "abc", "title": "茅台研究", "messages": [...]}'

# 列出 live
curl http://127.0.0.1:9999/conversations
# → {"ok": true, "conversations": [{"id": "abc", "title": "...", "updatedAt": ...}, ...]}

# 读一条
curl http://127.0.0.1:9999/conversations/abc

# 软删 (默认, 移到 _trash/)
curl -X DELETE http://127.0.0.1:9999/conversations/abc
# → {"ok": true, "permanent": false}

# 列回收站
curl http://127.0.0.1:9999/conversations/trash
# → {"ok": true, "conversations": [{"id": "abc", "deletedAt": 1700000000000,
#                                   "_trash_filename": "abc__1700000000000.json"}],
#    "purged": 0}    ← 顺便清掉 >30 天的, 这次清了 0 个

# 恢复 (默认挑最新副本)
curl -X POST http://127.0.0.1:9999/conversations/abc/restore \
  -H 'Content-Type: application/json' \
  -d '{}'
# → {"ok": true}

# 恢复指定副本 (多次删同 cid 时, 从 list_trash 拿 _trash_filename)
curl -X POST http://127.0.0.1:9999/conversations/abc/restore \
  -H 'Content-Type: application/json' \
  -d '{"trash_filename": "abc__1700000000000.json"}'

# 永久删 (跳过回收站, 不可恢复)
curl -X DELETE 'http://127.0.0.1:9999/conversations/abc?permanent=1'
# → {"ok": true, "permanent": true}
```

回收站行为:
- delete 默认 → 软删 (UI 上点 ❌ 用户期望可恢复)
- 30 天后 list_trash 时自动 purge (`purge_old_trash(ttl_days=30)`)
- restore 时如果 live 同 cid 已存在 (用户软删后又建同 cid 新会话), restore 自动加 `_restored_<ts>` 后缀避免覆盖

---

## /alerts + /alerts/check + DELETE /alerts/{id} — 盯盘价格预警

存 `~/.financial-analyst/alerts.yaml`. UI 自选股价格下添加 "跌破 X 提醒我" 调 `/run`
(走对话 add_alert tool), 列出走 `/alerts`, **删除走 `DELETE /alerts/{rule_id}`**.

```bash
# 列规则
curl http://127.0.0.1:9999/alerts
# → {"ok": true, "alerts": [{"id": "SH600519:price_below", "code": "SH600519", "kind": "price_below",
#                            "threshold": 1200, "note": "...", "desc": "茅台跌破1200"}]}

# 删规则 (UI ❌ 按钮调这个, v1.9.5 新增)
curl -X DELETE http://127.0.0.1:9999/alerts/SH600519:price_below
# → {"ok": true, "rule_id": "SH600519:price_below"}
# 删某 code 全部规则: DELETE /alerts/SH600519 (不带 :kind)

# 评估一次 (开盘内才真评估, 否则空)
curl http://127.0.0.1:9999/alerts/check
# → {"ok": true, "session": "open", "fired": [
#       {"id": "SH600519:price_below", "desc": "茅台跌破1200", "price": 1195.00, ...}
#   ]}
# 非交易时段: {"ok": true, "session": "closed", "fired": [], "note": "非交易时段, 不评估"}
```

添加规则**没有 POST /alerts** (避免单 REST 端点要复用 `add_alert` tool 的所有验证逻辑),
通过 `/run` 走 `add_alert` tool 在对话里加.

---

## GET `/tools` — 工具清单

```json
[
  {"name": "stock_brief", "cost": "instant", "desc": "Pull a stock's basic info ..."},
  {"name": "run_report", "cost": "minutes", "desc": "Run the deep-dive research swarm ..."},
  ...
]
```

`cost` ∈ `instant` / `seconds` / `minutes`. `mode=default` 时 `cost=minutes` 或
`confirm_required=true` 的工具会触发 `confirm_request`.

---

## GET `/models` — 可用 LLM

```json
{
  "ok": true,
  "models": [
    {"id": "qwen3.5-plus", "name": "qwen3.5-plus", "provider": "dashscope"},
    {"id": "qwen3-max",    "name": "qwen3-max",    "provider": "dashscope"},
    ...
  ],
  "by_provider": {
    "dashscope": ["qwen3.5-plus", "qwen3-max", "qwen3-coder-plus"],
    "deepseek":  [...],
    "anthropic": [...]
  }
}
```

UI 模型 picker 调这个. 注意: 不是所有 provider 都有 key (按 `.env` 决定).

---

## 限速 / 重试 / 缓存

`data/net.py` 提供统一基础设施:

- `domestic_session()` — `trust_env=False`, 直连 (适合 A 股域名, 不走 Clash)
- `intl_session()` — `trust_env=True`, 走系统代理
- `@rate_limited(source_name, cache_key=...)` 装饰器 — 单 source QPS 限制 + 指数退避重试 + TTL 缓存

预注册 source (per-source 默认):

| Source | QPS | Retries | Cache TTL | 用途 |
|--------|-----|---------|-----------|------|
| xueqiu | 1 | 3 | 30s | 评论 |
| xueqiu_hot | 2 | 3 | 60s | 热股榜 |
| tencent_quote | 5 | 2 | 2s | 实时行情 |
| tushare | 2 | 3 | — | 增量数据 |
| eastmoney_kuaixun | 2 | 3 | 15s | 快讯 |
| eastmoney_longhu | 1 | 3 | 600s | 龙虎榜 |
| sinafinance | 2 | 3 | 30s | 新浪财经 |
| ths_hot | 1 | 3 | 120s | 同花顺热股 |

线程安全 (`threading.Lock`). 累计统计经 `source_stats()` 返回, 出现在 `/diag` 响应里.

定义/扩展见 `src/financial_analyst/data/net.py::register_source`.

---

## CORS

开发期开放: `allow_origins=['*'], allow_methods=['*'], allow_headers=['*']`. 生产
部署建议限定到 GuanLan UI 的 origin.

---

## 版本

| 版本 | 时间 | 关键变动 |
|------|------|---------|
| v1.9.0 | 2026-04 | 初版 SSE bridge + /run + /confirm |
| v1.9.3 | 2026-05 | + session_id 多轮 + model 切换 + /conversations CRUD |
| v1.9.4 | 2026-05-23 | + /diag /report-progress /lesson, NaN-safe JSON, rate_limit 基础设施 |
