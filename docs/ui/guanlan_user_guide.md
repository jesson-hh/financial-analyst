# 觀瀾 (GuanLan) UI 用户手册

> 觀瀾是 financial-analyst 的桌面工作站前端 (React + Babel-Standalone 浏览器内编译,
> 通过 SSE 接 buddy 后端). 本文档教你 **怎么用**, 不教怎么开发.
>
> 开发者文档: 见 `G:/stocks/fa_ui_ready/START.md` 和 [`docs/api/sse_endpoints.md`](../api/sse_endpoints.md).

## 一、启动

### 方式 A — 一键 (推荐)

```bash
fa launch
```

`fa launch` 会:
1. 检查 `.env` 存在 (没有就先跑 `fa init`)
2. 后台启 `financial-analyst serve --port 9999`
3. 启 Web UI `python -m http.server 5173`
4. 自动开浏览器到 http://localhost:5173

关闭: Ctrl+C 即可 (两个子进程都会被干净停掉).

### 方式 B — 手动

```powershell
# 终端 1 — 后端
G:\financial-analyst\.venv\Scripts\financial-analyst.exe serve --port 9999
# 验证: http://127.0.0.1:9999/health → {"ok": true, "version": "1.9.4", ...}

# 终端 2 — 前端
cd G:\stocks\fa_ui_ready
python -m http.server 5173
# 浏览器开: http://localhost:5173
```

### 后端开关

`G:/stocks/fa_ui_ready/index.html` 第 64-65 行:

```javascript
// 留空 / 注释掉 = 跑本地 mock (6 只股票演示, 不依赖后端)
// 取消注释 = 连真后端
window.GUANLAN_BACKEND = 'http://127.0.0.1:9999';
```

**第一次跑 / Demo 演示**: 注释掉, 看 mock 数据走完 UI 流程.
**真正用**: 取消注释 + 起后端.

---

## 二、界面布局

```
┌──────────────────────────────────────────────────────────────────────┐
│  TopBar:  觀瀾  [🩺 探活]  [Model ▼: qwen3.5-plus]  [Sessions ▼]    │
├────────────────┬─────────────────────────────────────────────────────┤
│                │                                                       │
│  Sidebar       │  Transcript (主聊天区)                                │
│  ────────      │  ────────────                                         │
│  📜 会话列表   │  agent: 工具链 → 速览卡 → 答复                       │
│   • 茅台研究   │  user:  追问/指令                                     │
│   • BYD 走势   │  agent: ...                                          │
│                │                                                       │
│  ⭐ 自选股     │                                                       │
│   茅台 1332.95 │  ───  深度研报抽屉 (可弹出) ───                       │
│    -0.42%      │                                                       │
│   宁德 215.5   │                                                       │
│    +1.2%       │  Composer (底部输入)                                  │
│                │  [输入 / 或 / 指令]                  [模式 ▼] [发送] │
└────────────────┴─────────────────────────────────────────────────────┘
```

---

## 三、Composer (输入框)

### 自由输入
打字 → 回车发送. 后端按 `intent` 分类 (`quick_quote` / `deep_research` / `ask` /
`chat`) 调相应工具.

```
> 看下茅台怎么样
agent: [stock_brief] ✓ → 速览卡
       茅台目前 1332.95 元, PE 18.7, MV 1.67万亿...
       ...
```

### Slash 命令

| 命令 | 用途 | 例子 |
|------|------|------|
| `/mode safe` | 切到全确认模式. 每个工具调用前弹 y/n | `/mode safe` |
| `/mode default` | 默认模式. 只有 cost=minutes 工具 + 标记 confirm_required 的弹 | `/mode default` |
| `/mode auto` | 全自动. 跑研报这种慢工具不弹确认 | `/mode auto` |
| `/lesson <text>` | 沉淀对话经验. 写到 `memories/_shared/conversation_lessons.md` | `/lesson 大盘股不要给 +2 因子分` |
| `/clear` | 清当前会话 transcript (磁盘保留) | `/clear` |
| `/compact` | 把当前会话压缩成摘要, 释放 LLM 上下文 | `/compact` |

### Prefill 通道
某些 UI 按钮 (例如 "添加盯盘") 会把模板字符串塞进 Composer 让你确认再发送, 不直接
调后端. 见自选股 / 速览卡章节.

---

## 四、自选股 (Watchlist)

侧边栏右下方. 4 秒一次轮询 `/quotes`, 价格 + 涨跌幅实时刷新 (开盘内).

### 加股票
- 在速览卡里点 **"加入自选"** (或对话里 "把茅台加自选")
- 自选墙顶部输入代码 / 名称 (走 `/resolve` 解析)

### 删股票
- 鼠标悬停在条目上 → 出现 ❌

### 价格颜色
- 红色 = 涨 (A 股惯例, 不是西方红=跌)
- 绿色 = 跌
- 灰色 = 停牌 / 数据缺失

### 行为偏好
点击自选股条目 = 切到该股的研究视图. **不会自动 prefill 任何文本到 Composer**
(用户偏好: 只看不打扰).

---

## 五、速览卡 (Stock Brief)

agent 回复里嵌入. 包含:
- 名称 + 代码 + 现价 + 涨跌幅
- PE / PB / MV / 换手率 / 5日/20日/60日收益
- 三个按钮:
  - **加入自选** → 加到 watchlist (无需后端)
  - **添加盯盘** → 给 Composer prefill `{name}({code}) 跌破 ` 等你填阈值
  - **导出 markdown** → 触发浏览器下载 `{code}_brief.md`
- 雪球评论 (本地秒出) + 情绪条 (后台拉, 出现 sentiLoading 转圈, ~10-20s 出聚合)

### 评论刷新
两段式调用:
1. 切到该股 → `/comments?refresh=1&sentiment=0` (0.4s 拉评论快出)
2. 后台 → `/comments?refresh=0&sentiment=1` (10-20s 出情绪)

雪球评论拉不到 (anti-bot / 网络) → 显示 "雪球对该股近期无讨论".

---

## 六、深度研报抽屉

agent 跑 `run_report` 工具 → 自动从右边弹出抽屉, 实时显示进度:

```
深度研报 · SH600519 · 2026-05-23
═══════════════════════════════
[Tier 1] ████████████████████ 5/5  ✓ 0.4s
[Tier 2] ███████████░░░░░░░░░ 3/4  fundamental ✓
                                   technical ✓
                                   whale ⏳ running 45.2s
                                   quant ⏳ running 33.8s
[Tier 3] ░░░░░░░░░░░░░░░░░░░░ 0/4
[Tier 4] ░░░░░░░░░░░░░░░░░░░░ 0/1
───────────────────────────────
总进度: 8/14   用时: 47.5s
```

实现: 前端 `DeepReportProgress` 组件 1.5s 轮询 `/report-progress?code=X`. 状态切换:
`pending` → `running` → `done` / `fail`.

完成后:
- 抽屉自动出现 **"打开完整研报"** 按钮 → 拉 `/report?path=...` 渲染 markdown
- agent transcript 里出现 "研报已生成: §1 综合评级, §2 ..." 带跳转锚点

---

## 七、🩺 探活面板

TopBar 右上角 🩺 按钮 → 弹固定定位面板, 显示 5 源 + LLM 实时状态:

```
🩺 API 探活
═════════════════
xueqiu_comments    ✓  234ms   3 comments
xueqiu_hot_stock   ✓  187ms   5 hot stocks, top='贵州茅台'
tencent_quote      ✓  142ms   price=1332.95 pct=-0.42
news_db            ✓    8ms   1 recent news in db
llm                ✓  14210ms model=qwen3.5-plus reply='OK'
═════════════════
限速统计:
xueqiu        calls=12, cache_hits=8, retries=0
tencent_quote calls=47, cache_hits=30, retries=1
...
```

调 `/diag` (~20s 含 LLM) 或 `/diag?quick=1` (~2s 跳过 LLM).

**出问题时第一步**: 点这个看哪个源红了. 红的看 detail 字段是 timeout / WAF / 网络.

---

## 八、模型切换

TopBar 模型 picker 下拉 → 列 `/models` 返回的所有 provider × model. 切换:

- 影响**下一轮** /run 调用 (本轮已开始的不受影响)
- 不持久化 (浏览器刷新回到默认 qwen3.5-plus)
- 后端可用模型受 `.env` 里 key 决定. 没 key 的 provider 不会出现

> ⚠ 实测 qwen 系列除 `qwen3.5-plus` / `qwen3-max` 之外的型号 (`qwen-plus`,
> `qwen3-flash`, `qwen-turbo`, `qwen3.5-flash`) 阿里云百炼 API 都不支持 (返回
> BadRequestError). DeepSeek 在我们环境连不上 (SSL fail).

---

## 九、多会话 (Sessions)

TopBar Sessions 下拉. 每个 session 独立的:
- Transcript (聊天历史)
- BuddyAgent 实例 (在后端 24 LRU 缓存里)
- session_id (前端生成 UUID, 跟着每个 `/run` 请求传)

新建 / 切换 / 重命名 / 删除. 删除会调 `DELETE /conversations/{cid}` 后端也同步删.

**为什么 24 LRU**: 后端 BuddyAgent 有对话历史, 内存占用 ~5-50MB / session. 24 个
~1GB 上限. 超过踢最少用的.

---

## 十、价格预警 (盯盘)

### 加规则
- 速览卡里点 **"添加盯盘"** → Composer prefill `{name}({code}) 跌破 ` 让你填阈值
- 或对话直接说 "茅台跌破1200提醒我" → agent 调 `add_alert` 工具

### 查看规则
`/alerts` 返回当前所有规则. UI 自选墙下方有 "盯盘 (N)" 标签可点开看列表.

### 触发
开盘内, 前端每 N 秒 (默认 30) 调 `/alerts/check` → 命中规则的弹 toast + 推送到
transcript. 非交易时段 (`session != "open"`) 返回空, **不重复打扰**.

---

## 十一、键盘快捷键

| 按键 | 动作 |
|------|------|
| `Enter` | 发送 |
| `Shift+Enter` | 换行 (不发送) |
| `Esc` | 关掉当前确认框 (视为 n=拒绝) |
| `Ctrl+L` | clear current session transcript |
| `Ctrl+K` | focus Composer |
| `Ctrl+/` | toggle 🩺 探活面板 |

---

## 十二、故障排查

| 现象 | 可能原因 | 排查 |
|------|---------|------|
| 整页白屏 | babel-standalone 编译挂了 (jsx 语法错) | F12 → Console 看 error |
| 长时间转圈无回复 | LLM 慢 / 后端挂了 | 点 🩺 看 llm 是不是红 |
| "雪球对该股近期无讨论" 一直显示 | 雪球 anti-bot 拦了 | 点 🩺 看 xueqiu_comments 状态 |
| 自选股价格不更新 | 前端轮询挂了 / 后端 502 | F12 → Network 看 `/quotes` 是不是 200 |
| 加自选后页面没刷 | 浏览器缓存旧 app.jsx (改过没 hard refresh) | Ctrl+Shift+R 硬刷 |
| 报告抽屉一直 0/14 | `out/<code>_progress.json` 没写 | 检查 financial-analyst 进程是不是真在跑 |
| 模型切换不生效 | 后端 LLMClient.list_models() 没返回该模型 | 看 `/models` 响应 |

### 常见环境问题

#### Clash / VPN 拦截 localhost
某些版本 Clash 配 "全局代理" 时把 `localhost:9999` 也走代理出 502.

修复:
- Clash → Profile → 绕过域 → 加 `127.0.0.1, localhost, *.local`
- 或后端改 `--host 0.0.0.0` 用 `http://本机IP:9999`

#### 浏览器 CORS preflight 失败
- 后端 CORS 设的 `allow_origins=["*"]`, 不该出问题
- 出问题: 检查浏览器是否装了 CORS 拦截插件

#### 端口被占用
- `Error: [Errno 10048] Only one usage of each socket address`
- 改端口: `financial-analyst serve --port 9998` + 同步改 index.html L65

---

## 十三、隐私 / 安全

- **所有数据本地**. 后端跑在 127.0.0.1:9999, 不暴露公网
- **会话存盘**: `~/.financial-analyst/data/conversations/*.json` 明文, 包含 LLM
  原文. 不想留 → 手动删
- **API key**: 只读 `.env` 一次, 不出现在 SSE / 日志
- **报告**: 写到 `out/<code>_<date>.{md,json,html}`. 不上传任何云
- **memories**: 也都本地, 含 `_pending_introspections/`

---

## 十四、进阶 (开发者)

### 自定义快捷指令
`Composer` 里 slash 命令解析在 `app.jsx` 的 `handleSend()`. 加新命令:
1. 加 case 到 switch
2. 调对应后端 endpoint
3. 在本文档第三节加一行

### 自定义速览卡
`StockBriefCard` 组件在 `app.jsx`. 改字段 / 加按钮 / 调样式都在那.

### 接 SSE 自己写客户端
不用 React, 用 vanilla JS / Python / curl 都行. 协议见
[`docs/api/sse_endpoints.md`](../api/sse_endpoints.md). 关键: SSE 解析 `event:`
和 `data:` 帧, JSON.parse 时注意 NaN 已被后端替换成 null.
