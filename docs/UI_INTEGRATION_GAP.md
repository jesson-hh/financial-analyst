# 觀瀾桌面 UI ↔ financial-analyst agent — 接入 Gap 分析

> 设计: `finance analyst.zip` (Tauri + JSX 桌面原型, 当前是 mock adapter).
> 后端: financial-analyst v1.8.3 buddy agent (终端 TUI + 26 工具).
> 本文标注**接入需要做什么** + **设计要但 agent 缺的功能**, 供审核.

---

## 总览: 设计要的能力 vs agent 现状

| 设计能力 | agent 现状 | 结论 |
|---|---|---|
| 26 个工具 (名字/分类/耗时) | ✅ 完全一致 (设计基于我们的 FEATURES) | 无需改 |
| 自然语言 → LLM 选工具 → 中文总结 | ✅ `BuddyAgent.run_turn` | 无需改 |
| 流式事件 (工具开始/完成/回复累积) | ✅ run_turn 是 async generator yield events | 需转 SSE |
| 三档权限 default/safe/auto + y/n/a | ✅ `permission_mode` + `_confirm` | 需转 SSE 双向 |
| 斜杠命令 | ✅ /mode /model /reset /tools… | `/llm`→`/model` 起个别名 |
| 盯盘 + 触发 toast | ✅ alert engine + watch loop | 需独立服务化 |
| 多会话 + localStorage 持久化 | — 前端管, 后端无关 | ⚪ 前端搞定 |
| 暗色主题 / ⌘K 工具面板 / markdown 导出 | — 纯前端 | ⚪ 前端搞定 |

→ **工具层 100% 对得上**. 真正的工作在: ①把 agent 包成 HTTP/SSE 服务 (必做);
②6 个"设计要但我们缺"的功能 (需你审核)。

---

## 🟢 A. 必做接入工作 (不需审核 — 没这个接不了)

### A1. `financial-analyst --serve` HTTP/SSE 服务模式
设计的 Rust sidecar + 前端 fetch 都假设后端开 `127.0.0.1:9999` 的 `/run` SSE 端点.
我们现在只有终端 `chat`, 没有 HTTP 服务. **必须新写**一个 FastAPI(或 stdlib http)
服务, 把 `BuddyAgent.run_turn` 的 event 流映射成 SSE:

| agent event (现有) | → SSE event (设计期望) |
|---|---|
| (turn 开始) | `plan` {chain, intent, label} ← ⚠ 见 B1/B2 |
| `tool_call` | `tool_start` {idx} + `tool_done` {idx, result} |
| `text` | `answer_progress` {text} (累积) |
| `done` | `done` |
| `_confirm` 回调 | `confirm_request` + 等前端回 y/n/a (需双向, SSE+POST) |

工程量: **中** (1 个新模块 ~200 行 + 依赖 fastapi/uvicorn). 这是接入的地基.

### A2. 前端 adapter 换 mock → fetch SSE
`agent-adapter.jsx` 的 `GuanlanAgent.run` 按 README 示例换成 fetch + SSE 解析.
工程量: **小** (设计已写好示例代码).

### A3. 打包: PyInstaller → Tauri sidecar
把 financial-analyst 打成单可执行 + 配 `tauri.conf.json externalBin`.
工程量: **中** (PyInstaller 对 litellm/pandas/lightgbm 这种重依赖可能要调).

---

## 🟡 B. 设计要、但 agent 缺的功能 (请逐条审核 要 / 不要)

### B1. 预规划整条工具链 (`onPlan`)  —— ⚠ 架构差异
- **设计要什么**: 一开始就把整条工具链全部展示出来 (全 pending), 再逐个跑亮.
  这是觀瀾"研究纸带"视觉的核心 — 用户一眼看到"agent 打算查这 5 步".
- **我们现状**: `BuddyAgent` 是**边想边调** — LLM 每轮只决定下一个工具, 跑完看结果
  再决定下一个. **跑之前不知道整条链长啥样**.
- **加的话**: 要么 (a) 加一个"规划 LLM 调用" — 先让 LLM 列出打算用的工具链再执行
  (多一次 LLM 调用, 慢一点, 且 LLM 可能中途改主意); 要么 (b) 改成"边跑边把新工具
  append 进 chain" — 工具一个个冒出来而非一次全亮 (视觉上略不同, 但更忠实).
- **不加的降级**: 用方案 (b) — 前端 chain 从空开始, 每来一个 tool_start 就 append
  一行. 视觉略变 (不是一次全亮) 但**零后端改动**, 最忠实 agent 真实行为.
- **我的建议**: **不加预规划, 用降级 (b)**. 预规划是"假装知道未来", 反而不准.

### B2. 意图分类 `intent` (brief/fundflow/why_move/compare/technical/alert)
- **设计要什么**: 给每轮打个意图标签, 显示在工具链标题 ("资金流扫描"/"驱动归因").
- **我们现状**: 没有显式意图分类 — LLM 直接选工具, 不输出"意图"这个中间产物.
- **加的话**: 在 --serve 层加一个轻量分类 (规则正则, 像设计 mock 里的 detectIntent,
  ~20 行) 或让 LLM 顺带返回. 纯展示用, 不影响工具选择.
- **不加的降级**: 标题就用"研究中"/第一个工具名. 信息少一点但不影响功能.
- **我的建议**: **加规则版** (便宜, 20 行, 提升可读性). 不值得为它多调 LLM.

### B3. `stock_brief` 结构化输出  —— ⚠ 数据格式
- **设计要什么**: 速览卡片要**结构化 JSON 字段**: price/change/deltaPct/vol_ratio/
  turn/amp/pe/pb/roe/main_in/prev_main_in/industry/mc/xq_bull, 渲染成精美卡片
  (含主力资金 超大单/大单/中单/小单 分解条).
- **我们现状**: `stock_brief` 返回的是**拼接文本** (行情段+行业段+新闻段…), 不是 JSON.
- **加的话**: 给 stock_brief 加一个 `format='json'` 分支, 返回结构化 dict. 数据来源
  我们大都有 (quote/realtime/industry/fund_flow), 但**超大单/大单/中单/小单分解**
  我们 ggzjl 默认页没有 (设计里是 main_in×系数估算的), **xq_bull 雪球多头%我们没有**.
- **不加的降级**: 卡片改成展示 stock_brief 的文本 (markdown), 不做精美字段卡.
- **我的建议**: **加结构化输出** (卡片是核心体验), 但: 主力分解先用估算/留空,
  xq_bull 见 B6 单独决定.

### B4. `§N` 引用标注
- **设计要什么**: LLM 总结里关键数据点后标 `[§1][§2]`, 对应工具序号, 前端渲染成
  朱砂红印章脚注 (点击高亮对应工具). 这是觀瀾的标志性交互.
- **我们现状**: LLM 总结**没有 §N 引用** — system prompt 没要求.
- **加的话**: 改 buddy system prompt, 要求"关键数据后标 [§N], N 对应本轮第 N 个工具".
  设计 mock 的 prompt 已经示范了写法 (可直接抄). ~10 行 prompt 改动.
- **不加的降级**: 总结正常显示, 没脚注. 功能不减, 少个亮点交互.
- **我的建议**: **加** (便宜, 就改 prompt, 且是设计的招牌交互).

### B5. `run_report` 进度流  —— ⚠ 长任务体验
- **设计要什么**: 跑研报 5-8 分钟, 抽屉里显示**分步进度** (拉数据→训练→FM交叉→写报告→…)
  + 完成后全文.
- **我们现状**: `run_report` 是 subprocess 一次性跑完返回, **中间没有进度事件**.
- **加的话**: 要么 (a) 解析 report_v2.py 的 stdout 阶段日志转成进度事件 (需要底层脚本
  打印结构化阶段标记); 要么 (b) 前端用假进度条 (固定阶段 + 计时, 像设计 mock 现在做的).
- **不加的降级**: 用方案 (b) 假进度条 — 视觉有进度感, 实际只是计时. 完成后出真全文.
- **我的建议**: **先用降级 (b) 假进度**. 真进度要改底层 report_v2.py, 工程量大,
  收益主要是心理安慰, 优先级低.

### B6. `xq_bull` 雪球多头情绪 %
- **设计要什么**: 速览卡显示"雪球多头 68%"这种情绪指标.
- **我们现状**: 我们有雪球 hot_stock / comments 数据, 但**没有"多头百分比"**这个量化指标.
- **加的话**: 要么爬雪球的"看涨/看跌"投票 (需确认雪球有这个公开数据 + opencli 能拿),
  要么用评论情感分析算一个 (要 LLM 跑情感, 慢且贵).
- **不加的降级**: 卡片这个字段留空 / 不显示.
- **我的建议**: **暂不加** — 数据源不确定, 性价比低. 卡片先不显示这个字段.

---

## 🟢 C. 命名/小差异 (接入时顺手对齐, 不需审核)

| 设计 | 我们 | 处理 |
|---|---|---|
| `/llm` 切模型 | `/model` | --serve 层加 `/llm` 别名 |
| 工具 args 用 `{symbol:"300750"}` | 我们用 `code="SH600519"` | adapter 层做 code 格式转换 (300750↔SH300750) |
| `target:"stock"` (资金流) | 我们 `target="gegu"` | 映射表 stock→gegu / concept→gainian / industry→hangye / big→ddzz |

---

## 建议执行顺序 (审核通过后)

1. **A1 --serve SSE 服务** (地基) — 含 B1 降级(b) + B2 规则意图 + B4 §N prompt
2. **B3 stock_brief 结构化** (卡片数据)
3. **A2 前端 adapter 换 SSE** — 真连通
4. **A3 PyInstaller 打包 sidecar** — 出 .msi
5. B5 假进度 / B6 跳过

---

## 需要你拍板的 6 个点 (回复 要 / 不要 / 按我建议)

| # | 功能 | 我的建议 |
|---|---|---|
| B1 | 预规划整条链 | **不加**, 用边跑边 append 降级 (更忠实) |
| B2 | 意图分类标签 | **加规则版** (20行, 便宜) |
| B3 | stock_brief 结构化卡片 | **加** (核心体验) |
| B4 | §N 引用脚注 | **加** (改 prompt, 招牌交互) |
| B5 | run_report 真进度 | **不加**, 用假进度条降级 |
| B6 | xq_bull 雪球多头% | **不加** (数据源不确定) |

> 你可以直接说"按你建议", 或逐条改. 拍板后我开始 A1 (--serve 服务).
