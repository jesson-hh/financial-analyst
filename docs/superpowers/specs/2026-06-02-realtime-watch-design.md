# 实时盯盘系统 (Realtime Watch) — 设计 spec

> 2026-06-02 · 落在 **fa** (`src/financial_analyst/`) · 分支 `merge-stocks`(未 push)· brainstorming 产出,**待用户过目 → 再 writing-plans**。

**Goal:** 一个盘中实时盯盘**主界面 + agent advisor**:盯自选股,每分钟刷行情/新闻,**事件触发**时 agent 即时研判那只票,在界面弹出 **买/卖/持 + 理由 + 止损/目标**,人确认/忽略(**无自动执行**)。

**Architecture:** `fa serve`(FastAPI/buddy)进程内一个**盘中后台 asyncio loop**(每 60s tick)→ 拉行情(Tencent 快照 + pytdx 5min K线)+ 轮询新闻 → `IntradayTrigger` 评触发 → 触发的票喂**轻量 `WatchAgent`** → SSE 推到 quant 工作台新「盯盘」页。复用 ~70%(触发器/决策 LLM/SSE/行情/自选),新写 ~30%(tick loop / WatchAgent 单股封装 / K线蜡烛图 / 盯盘页 / 推荐日志)。

**Tech Stack:** Python 3.13(fa `.venv`)· FastAPI + SSE · asyncio · pytdx + Tencent HTTP · React(babel in-browser,`quant.jsx`)· parquet(推荐日志)。

---

## 1. 范围 (v1)

**IN:** advisor(人确认)· 盯 watchlist · 事件触发 · 输入=实时行情 + 昨收因子/技术 + 主力 + 实时新闻 · buddy 内置后台 loop + SSE · quant 新页 + 1min/5min 蜡烛 K线 + 推荐 feed · 推荐+确认日志。

**OUT(v1 明确不做):** 真实下单 / 自动执行 · 全市场扫描 · 实时资金流·龙虎榜·F10 事件 · 组合优化/风控仓位 · 多用户。

## 2. 锁定决策(brainstorming)

| 维度 | 决策 |
|---|---|
| agent 自主度 | **决策建议(人确认)**,无执行/持仓/风控层 |
| 盯的范围 | **watchlist**(复用 `watchlist.parquet`) |
| 决策触发 | **事件触发**(复用回测触发器 + 新闻命中) |
| agent 输入 | 实时行情 + 昨收因子/技术/主力 + **实时新闻流** |
| 运行模型 | **buddy 内置盘中后台 asyncio loop + SSE** |
| 归属 | fa(复用 buddy/quant UI/backtest decision·intraday/pytdx·Tencent/watchlist) |

## 3. 数据流

```
盘中每 60s tick (9:30-11:30 / 13:00-15:00):
  read watchlist.parquet
      │
      ├─ Tencent 批量快照 (1 次 HTTP) ──► 实时价/涨跌/量比 → SSE quote_update (驱动左栏)
      ├─ 每只票 pytdx fetch_5min (限并发) ──► 5min bars
      └─ 轮询新闻增量 (命中盯的票)
              │
       IntradayTrigger.check(每只票)  +  新闻命中 → 合成 news 触发
              │  (受 per-code cooldown + 每时段全局 LLM 上限 限流)
        触发的票 → WatchAgent.decide_one(WatchContext) ──(async LLM)──►
              │
        WatchRec(action/target/stop/reason/confidence)
              │
       SSE recommendation 事件 ──► 右栏 feed [确认][忽略]
              │                                   │
       写 watch_recommendations.parquet ◄────── POST /watch/ack
```

## 4. 组件(复用 vs 新写,带真实接口)

### 复用(接口已核实)
| 组件 | 文件 | 关键接口 |
|---|---|---|
| 实时快照 | `data/collectors/tencent_quote.py` | `TencentQuoteCollector.fetch(codes)→{code:{price,changePercent,volume【手】,vol_ratio,high,low,amount,...}}`(HTTP GBK,~120ms/批,无 token) |
| 5min K线 | `data/updaters/pytdx_kline.py` | `fetch_5min(client,code,n_bars=240)→[{datetime,open,high,low,close,vol【股】,amount}]`(⚠ vol=股,转手÷100) |
| 触发器 | `backtest/intraday.py` | `IntradayTrigger.check(code,bars_upto_t,position,sellable_qty,i)→TriggerEvent(kind∈{stop_break,breakout_high,volume_surge},is_risk,detail,metric,bar_index)`;`IntradayTriggerConfig`(max_per_day_per_code/global、breakout_min_gain_pct=.008、volume_surge_*);`reset_day()` |
| 决策 schema | `backtest/decision.py` | `DecisionLeg(code,action∈{buy,add,hold,reduce,sell},target_price,stop_loss,weight_pct,reason)`;LLM client + `DecisionCache` 模式可复用 |
| SSE/后台 | `buddy/server.py` | `/run` 的 `StreamingResponse(media_type="text/event-stream")` + `_safe_json_dumps`(SSE frame)+ `asyncio.create_task` 后台 + `asyncio.to_thread` |
| 自选 | `data/updaters/watchlist.py`,`backtest/candidate.py` | `watchlist.parquet`(code,source_file,position,sync_time);`_load_watchlist_codes(cfg)` |
| 前端框架 | `ui/quant.jsx` + `quant.html` | `QuantApp` `mode` + `TopBar.tabs` + `?v=` cache-buster;`window.GUANLAN_BACKEND`;`EquityChart/ICChart/DecileChart`(净值/柱,**非蜡烛**) |

### 新写
| 组件 | 说明 |
|---|---|
| `WatchItem` | 轻量关注项:`code` + 可选 `avg_cost/stop_loss`(让 stop_break 在无组合时可用) |
| `WatchLoop` | 盘中 tick 调度:拉数据 + 评触发 + 限流 + 调 agent + 入 SSE 队列 + 写日志 |
| `WatchAgent` | **轻量单股 advisor**:复用 LLM client + 决策 schema,输入是单股实时上下文(不要组合 candidates/holdings/cash/nav) |
| 实时新闻轮询 | 盘中复用 news 源(tdx_f10 增量 + akshare 快讯),命中盯的票=触发+输入 |
| `/watch/*` 端点 | SSE stream + 控制(start/stop/status、add/remove item+set stop、ack 推荐) |
| 盯盘前端页 `WatchMode` | 3 栏:自选+实时价+⚡触发 \| 蜡烛 K线+指标 \| 建议 feed + 确认/忽略 |
| 蜡烛 K线组件 | 新(`EquityChart` 是净值线);SVG 蜡烛 + MA/MACD/量 叠加 |
| 推荐日志 | `watch_recommendations.parquet` |

## 5. WatchAgent 设计(单股 advisor,轻量)

```
WatchContext(输入):
  code, name, now_ts
  trigger: {kind, detail, metric}            # 来自 IntradayTrigger / 新闻命中
  realtime: {price, change_pct, vol_ratio, 分时量价}     # Tencent 快照
  bars_5min: 近 N 根 [{datetime,o,h,l,c,vol}]            # pytdx
  factors_eod: {RSI, MACD, MA5/20/60, BOLL, OBV, 量比, ...}  # 昨收算好 (复用 research 因子/技术)
  news_today: [命中盯的票的当日新闻摘要]
  watch_item: {avg_cost?, stop_loss?}        # 可选, 用户设的关注成本/止损

WatchRec(输出, 复用 DecisionLeg + confidence):
  code, action∈{buy,add,hold,reduce,sell}, target_price, stop_loss, reason, confidence(0..1)

实现: 复用 decision.py 的 LLM client + cache 模式;新 prompt(单股、advisor 口吻、
      要求明确 action + 止损 + 目标 + 理由,且理由必须关联触发原因)。
      async decide_one(ctx) -> WatchRec。
```

## 6. 触发适配(无组合)

- `breakout_high` / `volume_surge`:不需 position,直接用 `fetch_5min` 的 bars。
- `stop_break`:需要 `stop_loss` → 来自 `WatchItem`(用户设)或 agent 上次建议的止损;用 `WatchItem` 造一个轻量 `Position` 传给 `check()`。无止损的 watch item 跳过 stop_break。
- 复用 `IntradayTriggerConfig` 的 caps + `reset_day()`(每日重置)+ per-code cooldown。

## 7. 后端:tick loop + SSE

- `WatchLoop`(asyncio task,buddy 启动时按市场时段调度;9:30–11:30 / 13:00–15:00 每 60s tick;盘后/非交易日 idle)。
- 每 tick:读 watchlist → Tencent 批量快照(1 次 HTTP)→ 每只票 `fetch_5min`(限并发,复用 client)→ `IntradayTrigger.check` → 触发的票(受 cooldown/cap 限流)→ `await WatchAgent.decide_one` → 推荐入 SSE 队列 + 写日志。
- 新闻:每 tick(或每 N tick)轮询 news 增量,命中盯的票 → 合成 news 触发。
- `/watch/stream`(SSE):事件 `quote_update` / `trigger` / `recommendation` / `heartbeat`。
- 控制端点:`POST /watch/start`、`/watch/stop`、`GET /watch/status`、`POST /watch/item`(add/remove + set stop)、`POST /watch/ack`(确认/忽略一条推荐)。
- **成本护栏**:per-code cooldown(同票同类触发后 N 分钟内不再研判)+ 每时段全局 LLM 调用上限(沿用 `IntradayTriggerConfig.max_per_day_*` 思路);数据源失败跳过不崩。

## 8. 前端:盯盘页

- `TopBar.tabs` 加 `{k:'watch', l:'实时盯盘'}`;`QuantApp` 加 `{mode==='watch' && <WatchMode/>}`;`quant.html` bump `?v=`。
- 3 栏:**左** 自选列表(SSE `quote_update` 驱动实时价/涨跌/⚡触发标记,点选切换中栏)· **中** 选中票 5min(或 1min)蜡烛 K线 + MA/MACD/量 叠加(新组件)· **右** agent 建议 feed(SSE `recommendation` 流,每条:时间/票/触发/action/理由/止损·目标 + `[确认][忽略]` → `POST /watch/ack`)。
- 触发提示:⚡标记 + 可选声音/弹框(轻量)。

## 9. 持久化

`watch_recommendations.parquet`:`ts, code, trigger_kind, action, target_price, stop_loss, reason, confidence, user_action(confirm/ignore/none), user_action_ts`。供事后复盘 + 未来学习 agent 准确率。

## 10. 容错/降级

- 行情源 timeout(Tencent/pytdx):跳过该 tick 该票,log warning,**不崩 loop**。
- LLM 失败:该推荐标 `error`,feed 显示"研判失败",不阻塞其它票。
- 非交易时段:loop idle(交易日历判断,复用 research calendar 或简单时段判断)。
- buddy serve 重启:loop 重起,状态从 watchlist + 当日日志恢复;当日已发推荐 dedup 不重发。

## 11. 测试策略

- `IntradayTrigger`:复用回测已有单测。
- `WatchAgent`:mock LLM client,给定 `WatchContext` → 断言输出 schema + action 合理(仿 backtest decision mock)。
- tick loop:**假行情源**(stub `TencentQuoteCollector`/`fetch_5min` 返回构造 bars)→ 端到端 1 tick 触发 → 推荐入队(仿回测 stub-loader,不依赖实时网络/key)。
- SSE:httpx ASGI 探针,订阅 `/watch/stream`,触发后收到 `recommendation` 事件。
- 限流:构造连续触发 → 断言 cooldown/cap 生效。
- 前端:babel 编译 `quant.jsx` 通过 + 浏览器实测(Claude Preview)盯盘页渲染 3 栏 + 假 SSE。
- **回归基线**:改动后 fa 全量仍 `1180 passed / 3 skipped`(+ 新增 watch 测试)。

## 12. 待确认默认值(spec review 时定)

1. **实时新闻源**:先 `tdx_f10` 增量 + akshare 快讯(最轻);轮询频率(每 tick? 每 5 tick?)。
2. **K线周期**:5min(`fetch_5min` 现成,最稳)还是 1min(需确认 1min 实时源)?题述"k线",建议 v1 用 5min。
3. **tick 间隔**:60s(题述"每分钟")。
4. **触发阈值**:沿用回测默认(breakout 0.8% / volume 3×),盯盘页可调。
5. **限流**:单股 cooldown 默认 15min?每时段全局 LLM 上限默认值?
6. **提示**:要不要声音/弹框?

## 风险 / note

- **vol 单位坑**:`pytdx fetch_5min` vol=**股**,Tencent=**手** — 喂触发器/显示前要统一。
- **新闻去重**:盯盘是实时(不像回测要防未来函数),但同一新闻多 tick 命中要去重。
- **LLM 延迟**:触发→研判有 LLM 往返(秒级),feed 先显示"研判中"再补结果。
- 这是 fa 功能,建在 `merge-stocks`(未 push);不碰 `main`;遵守"翻私有前禁 push"红线。
