# 实时盯盘 Panel UX 改造 (P0+P1+P2) · 设计

> 状态: 设计审中
> 日期: 2026-06-03
> 子项目: financial-analyst 量化工作台 — 实时盯盘 panel (WatchMode)
> 工作量: ~1 天 (P0 半天 + P1 1/4 天 + P2 1/4 天)
> 触发事件: backtest-panel-ux 完工后做同款改造 (ROI 表 D 项, 用户授权 "push 完做 D 和 E")

## 目标

WatchMode 已经能跑 (3 栏 live + WatchReview 复盘页 + SSE 实时 quote + 推荐 feed + 自选 add/del + 命中率回填), 但**类似 backtest 的"不知道在干什么"问题**:
- 没说清盯盘的工作流: tick → 事件触发 → LLM → 推荐. 用户看到 ⚡ 不知道触发了什么
- 状态横条只有 "N 只 · tick X · SSE 状态" — **不显示当前 tick_seconds / cooldown / LLM cap 配置**, 用户改不了也不知道默认值
- RecCard 显示信息有限 (reason 单行 + 4 个小 chip), 看不到完整 LLM raw / trigger_kind 详情
- 自选列表 chip 不能编辑 avg_cost / stop_loss (后端字段已支持但前端没暴露), 浮动收益不显示
- 4 KPI (现价/涨跌%/最高/最低) 无 tooltip 解释来源
- `tick_seconds / cooldown_minutes / global_llm_cap_per_session` 后端 `WatchStartReq` 都支持但前端 hardcoded 不传 → 永远跑默认 (60s / 15min / 20 次)

本 spec 做**与 backtest UX 同款 3 档透明化**, streaming 场景特化:
- **P0** 让用户**看懂盯盘在干什么** (banner + 状态横条增强 + 推荐 modal)
- **P0 轻后端**: `/watch/status` 返当前 cfg (tick_seconds/cooldown/cap), 让前端能显示
- **P1** **完整透明** (自选 chip 编辑 + ⚡ trigger 详情 + KPI tooltip)
- **P2** **参数完整可调** (高级控件折叠区 tick_seconds/cooldown/llm_cap)

## 范围

### 做
- 后端 `/watch/status` 加 3 字段 (tick_seconds/cooldown_minutes/global_llm_cap_per_session) 返当前 cfg
- `quant.jsx::WatchMode` 加 5 个新 sub-component: WatchStrategyBanner / WatchStatusChips / RecDetailModal / TriggerPopover / WatchAdvancedControls
- 现有 RecCard 加 onClick 接 modal
- 现有自选 chip 加 avg_cost/stop_loss 编辑入口 + 浮动收益显示
- 现有 4 KPI (现价/涨跌%/最高/最低) 加 tooltip prop
- start payload 传 advanced 参数
- `quant.html` ?v= bump
- Playwright 真浏览器烟测

### 不做 (留下轮)
- ❌ WatchReview 复盘页 (现已实现, 不动)
- ❌ Mock advisor 后端 (Watch 现无 mock 模式; 添加是后端独立工作, 留下轮)
- ❌ 多 watch loop 并发 (架构是 single loop singleton, 不改)
- ❌ 5min 蜡烛重构 (现 SVG Candle 简单够用)
- ❌ trigger metadata API (现 `trigger_kind` 是字符串如 "price_drop_30m", spec 里只静态描述, 后端不开新 endpoint 返完整 trigger 说明)
- ❌ 历史相似事件命中率 inline 显示 (留 WatchReview 复盘页, modal 不重复)

---

## P0 看懂盯盘在干什么 (~半天)

### P0.1 顶部 banner

`WatchMode` 控件条下方加固定栏 (running=true 时显示, running=false 时折叠到一行 hint):

**Running banner**:
```
┌───────────────────────────────────────────────────────────────────┐
│ 🔴 实时盯盘运行中 — Tencent realtime + 事件触发 + LLM 决策         │
│                                                                   │
│ 每 60s tick: 拉自选最新行情 → 跑事件触发器 → 触发即调 qwen3.5-plus│
│ 出 5 档建议 (buy/add/hold/reduce/sell) → SSE 推到右侧 confirm/    │
│ ignore.                                                           │
│                                                                   │
│ 防刷: 同 (code, trigger_kind) 冷却 15 min · 全 session 最多 20 次  │
│ LLM 调用                                                          │
└───────────────────────────────────────────────────────────────────┘
```

**Stopped hint** (运行前):
```
┌───────────────────────────────────────────────────────────────────┐
│ ⚪ 盯盘未开始 — 左侧自选列表添加股票, 按 ▶ 开始盯盘                 │
│                                                                   │
│ 工作流: 每 60s tick 拉行情 → 事件触发 → LLM 出 5 档建议 → 你确认  │
└───────────────────────────────────────────────────────────────────┘
```

样式: 单 Panel, light-paper 底色, 边框 `--line`, running 时 `border-left: 3px solid var(--zhu)` (红盘色), stopped 时 `var(--ink-3)` 灰色.

### P0.2 状态横条增强 + 后端 status 返 cfg

**后端 P0.2 改 `/watch/status` (server.py L2435-2447)** 加 3 字段:
```python
return JSONResponse({
    "ok": True,
    "running": _watch_running(),
    "n_items": len(items),
    "items": items,
    "tick_count": int(getattr(loop, "tick_count", 0)) if loop else 0,
    "llm_calls_made": int(getattr(loop, "llm_calls_made", 0)) if loop else 0,
    # P0.2 新增 (让前端显示当前 cfg)
    "tick_seconds": float(getattr(getattr(loop, "cfg", None), "tick_seconds", 60)) if loop else 60,
    "cooldown_minutes": int(getattr(getattr(loop, "cfg", None), "cooldown_minutes", 15)) if loop else 15,
    "global_llm_cap_per_session": int(getattr(getattr(loop, "cfg", None), "global_llm_cap_per_session", 20)) if loop else 20,
})
```

**前端**: 顶部控制条已有 `{items.length} 只 · tick {tickCount}` chip. 替换为 BacktestSummaryChips 同款一行 chip 串:

```
┌────────────────────────────────────────────────────────────────┐
│ 自选 5 只 · tick 142 · SSE ●已连                                │
│ tick 60s · 冷却 15min · LLM 12/20 次 (剩 8 次)                  │
│ 推荐 12 条 · 已确认 3 / 忽略 2 / 待处理 7                       │
└────────────────────────────────────────────────────────────────┘
```

让用户**一眼看清**当前节奏 / cap 剩余 / 推荐处理情况.

### P0.3 RecCard 点击 → modal

**现状**: `RecCard` (L2307) 单卡显示 reason 单行 + 4 chip + confirm/ignore. 点击无效.

**改造**: 卡片可点击 → modal:
```
┌────────────────────────────────────────────────┐
│ 2026-06-03 14:32 · SH600519 · buy       ×     │
├────────────────────────────────────────────────┤
│ 触发原因 (trigger_kind)                         │
│ ┃ price_drop_30m: 价格 30 分钟内跌幅超 2%       │
│ ┃ 阈值: 默认 -0.02; 当前实测: -2.4%             │
│                                                │
│ LLM 决策                                        │
│ ┃ buy · 50% pct · 信心 78%                      │
│ ┃ 目标价: 1812.00 · 止损: 1650.00               │
│                                                │
│ LLM reason                                     │
│ ┃ 茅台中午盘急跌触发短线反弹机会, MA20 支撑     │
│ ┃ 1680 附近, 估值已回到合理区间, 短线博弈反弹.  │
│                                                │
│ LLM 返回原文 (raw JSON)                         │
│ ┃ {                                            │
│ ┃   "action": "buy",                            │
│ ┃   "target_price": 1812.00, ...                │
│ ┃ }                                            │
│ ┃ [展开/折叠]                                    │
│                                                │
│ 历史相似 (该 trigger_kind 在 WatchReview 见详)  │
└────────────────────────────────────────────────┘
```

数据已存在: `item.rec` 完整字典 (含 action / reason / trigger_kind / target_price / stop_loss / confidence / raw?). 前端只需切片显示.

如果 `item.rec.raw` 不存在 (后端只返解析后字段不返 raw), modal 不显示 "raw JSON" section.

---

## P1 完整透明 (~1/4 天)

### P1.1 自选 chip 增强 (avg_cost / stop_loss / 浮动收益)

**现状**: 自选列表 chip (L2675-2699) 只显示 code + ⚡fired + price + changePercent + ×移除. avg_cost / stop_loss 后端 `WatchItemReq` 字段都有, 前端 hardcoded `{code: c}` 不传.

**改造**:
1. chip 旁加 "⋯" 按钮 → popover 编辑 avg_cost / stop_loss
2. 编辑后调 `/watch/item` op=remove 再 op=add 带新字段 (或加新后端 op=update — 简化版用 remove+add)
3. 显示浮动收益 (price - avg_cost) / avg_cost · 涨绿/跌红
4. 触发"持仓收益跌破 stop_loss" 这种 trigger_kind 才有意义 — 这是给后端 advisor 的额外信号

UI (chip 内):
```
┌───────────────────────────────────────┐
│ SH600519  ⚡          1685.00 +0.42% │
│ price_drop_30m         成本 1700.00  │ ← avg_cost
│                        浮动 -0.88%   │ ← (price-cost)/cost
│                        止损 1600.00  │ ← stop_loss
│ [⋯ 编辑]                       [×移除]│
└───────────────────────────────────────┘
```

### P1.2 ⚡ trigger 点击详情 popover

**现状**: `<span title={fired[code]}>⚡</span>` 用 HTML title 显示 trigger_kind 字符串 (eg "price_drop_30m"). 用户不知道这意味着什么.

**改造**: ⚡ 点击 → 小 popover 显示:
```
┌───────────────────────────────┐
│ 上次触发 (14:32)               │
│ trigger_kind: price_drop_30m  │
│ 含义: 价格 30 分钟内跌幅超阈值 │
│ 默认阈值: -2%                  │
│ 已触发 3 次 (本 session)        │
└───────────────────────────────┘
```

trigger_kind 元数据 (含义 + 默认阈值) 写成**前端静态 dict** (常见 6-8 个 kind), 不开新后端 endpoint. 未触发过的 kind → "未识别的 trigger_kind, 见后端 signals.py".

### P1.3 4 KPI tooltip

蜡烛下方 4 KPI (现价/涨跌%/最高/最低 @ L2707-2712) 加 tooltip:
- 现价 — "Tencent realtime 推送的最新成交价 (盘中) / 上一交易日收盘 (盘后)"
- 涨跌% — "(price - prev_close) / prev_close · prev_close 来自 Tencent realtime"
- 最高 — "当日最高价 (Tencent realtime 累计)"
- 最低 — "当日最低价 (Tencent realtime 累计)"

复用 backtest panel 已扩展的 `Kpi` component (tooltip prop 已支持).

---

## P2 参数完整可调 (~1/4 天)

### P2.1 高级控件折叠区

**现状**: 顶部控制条只有 `▶ 开始盯盘 / ■ 停止 / [盯盘|复盘] segment / + 添加`. 没有 tick/cooldown/cap 控件.

**改造**: `+ 添加` 按钮旁加 [高级 ▾] 按钮, 展开后显示:

```
┌────────────────────────────────────────────────────────────────┐
│ tick 间隔 [60] 秒   冷却 [15] min   LLM cap [20] 次/session    │
│                                                                │
│ 注: 修改后下次 ▶ 开始盯盘 生效 (running 中无法热改)             │
└────────────────────────────────────────────────────────────────┘
```

- tick_seconds: number input, 1-600 (1s ~ 10min)
- cooldown_minutes: number input, 1-120 (1min ~ 2h)
- global_llm_cap_per_session: number input, 1-200

**关键约束**: running 中改不生效. 启动时传 `WatchStartReq.tick_seconds/cooldown_minutes/global_llm_cap_per_session`. 停了改, 重启生效.

UI: 在控件 disabled 状态 (running=true 时), 加 tooltip "运行中无法热改 — 请停止后再修改".

### P2.2 start payload 透传

`start()` 函数 (L2538) 当前 body:
```js
{ items: codes.map(c => ({ code: c })) }
```

改为:
```js
{
  items: codes.map(c => ({ code: c, avg_cost: ..., stop_loss: ... })),  // P1.1 chip 编辑的 avg_cost/stop_loss
  tick_seconds: tickSeconds || null,         // null 走后端默认
  cooldown_minutes: cooldown || null,
  global_llm_cap_per_session: llmCap || null,
}
```

---

## 跨切关注

### 数据契约

后端 `/watch/status` 加 3 字段保持向后兼容 (老前端不读不破坏). `RecCard` modal 显示 `item.rec` 已存在的字段, 不要求 backend 加新字段. 唯一 hard 后端改动是 status 加 3 字段.

### 提交策略

- 单分支 `feat/watch-panel-ux` (从 main `edc02a4` 派生)
- 3 commit (类似 backtest):
  1. `feat(watch): /watch/status 返当前 cfg (tick_seconds/cooldown/llm_cap) + 测试`
  2. `feat(ui): WatchMode P0+P1+P2 — banner/状态横条/推荐 modal/自选 chip 编辑/trigger popover/KPI tooltip/高级控件`
  3. `test(e2e): watch panel P0+P1+P2 Playwright 烟测 (banner/横条/modal/popover/高级)`
- 不推 origin (按 "保留等一起推" 流程)

### 测试

| 文件 | 覆盖 |
|------|------|
| `tests/test_watch_status_cfg.py` (new) | `/watch/status` 在 idle 返默认值 (60/15/20), running 后返实际 cfg (eg 30/10/15) |
| Playwright `tests/test_watch_panel_ux_e2e.py` (new) | 切到 Watch tab → banner 文案 (stopped 版) → 加 1 只股 → [高级 ▾] 展开改 tick=30 → ▶ 开始 → banner 切 running 版 + 横条显示 "tick 30s" → 关 |

### 验收 DoD

- [ ] `/watch/status` idle 返 tick_seconds=60 / cooldown_minutes=15 / global_llm_cap_per_session=20
- [ ] `POST /watch/start {tick_seconds: 30}` 后 status 返 tick_seconds=30 (cfg 透传)
- [ ] 前端 stopped banner 显示 "盯盘未开始 — ..." 灰色
- [ ] 前端 running banner 显示 "实时盯盘运行中 — Tencent realtime + ..." 红色
- [ ] 状态横条显示 tick / 冷却 / LLM cap 剩余
- [ ] 点击 RecCard → modal 显示完整 reason + trigger_kind 描述 + target/stop/confidence
- [ ] 自选 chip 显示 avg_cost / stop_loss / 浮动收益 (若 avg_cost 已填)
- [ ] ⚡ 点击 → popover 显示 trigger_kind 含义
- [ ] hover KPI "现价" → tooltip 显示 "Tencent realtime ..."
- [ ] 高级控件 [高级 ▾] 展开 3 控件, running 中 disabled
- [ ] Playwright 烟测全过
- [ ] `quant.html` ?v= bump
- [ ] 全量回归不破
- [ ] 工作分支 feat/watch-panel-ux, main 不动, 不推 origin

### Workflow 编排

3 阶段串行 (避免 Phase 0 并行原罪, 复用 backtest 经验):
1. **T1 后端** (1 agent, ~1h): server.py `/watch/status` 加 3 字段 + 1 个测试. **轻**.
2. **T2 前端** (1 agent, ~半天): quant.jsx WatchMode 加 5 sub-component (WatchStrategyBanner / WatchStatusChips / RecDetailModal / TriggerPopover / WatchAdvancedControls) + 自选 chip 增强 + KPI tooltip + start payload + quant.html ?v= bump
3. **T3 Playwright 验证** (1 agent, ~半天): test_watch_panel_ux_e2e.py + 全量回归
