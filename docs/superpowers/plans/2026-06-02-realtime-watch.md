# 实时盯盘系统 (Realtime Watch) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development`(推荐)或 `executing-plans` 逐任务执行。步骤用 checkbox 跟踪。每个任务 = 一个 TDD 循环 + 一次 commit。**fa python = `G:/financial-analyst/.venv/Scripts/python.exe`**;测试 `pytest tests/ -q`。改动后 fa 全量须仍 `1180 passed / 3 skipped` + 新增 watch 测试。**建在 `merge-stocks` 分支,不 push、不合 main**(私有红线)。commit **不加 Co-Authored-By**。

**Goal:** 盘中实时盯盘 advisor:盯 watchlist,每 60s tick 刷行情/新闻,事件触发时轻量 agent 即时研判那只票,SSE 推到 quant 新「盯盘」页弹 买/卖/持+理由+止损/目标,人确认/忽略(无自动执行)。

**Architecture:** `fa serve` 进程内盘中后台 asyncio loop(`WatchLoop`)→ Tencent 快照 + pytdx 5min → `IntradayTrigger`(复用)+ 新闻命中 → `WatchAgent`(新,单股,复用 LLM client)→ SSE `/watch/stream` → 前端 `WatchMode`。

**Tech Stack:** Python 3.13(fa `.venv`)· FastAPI/buddy + SSE · asyncio · pytdx + Tencent HTTP · React(babel in-browser)· parquet。

**Spec:** `docs/superpowers/specs/2026-06-02-realtime-watch-design.md`(§12 默认值已确认:K线 5min / 新闻 tdx_f10+akshare 每 5 tick / tick 60s / 触发沿用回测默认 / cooldown 15min·全局 ~20 / ⚡+轻量声音可关)。

---

## File Structure

**新建 `src/financial_analyst/watch/`**(新包,独立于 `backtest/`,复用其纯逻辑):
- `__init__.py`
- `models.py` — `WatchItem` / `WatchContext` / `WatchRec`(dataclass)
- `store.py` — 推荐日志 parquet 读写
- `triggers.py` — 无组合触发适配(合成 Position 喂 `IntradayTrigger` + 新闻触发)
- `feed.py` — 实时数据拉取封装(Tencent 快照 + pytdx 5min,vol 单位归一)
- `agent.py` — `WatchAgent`(单股 advisor,复用 `backtest/decision.py` 的 LLM client/cache 模式)
- `loop.py` — `WatchLoop`(tick 编排 + cooldown/cap + 市场时段)

**修改:**
- `src/financial_analyst/buddy/server.py` — 加 `/watch/*` 端点 + 后台 task
- `src/financial_analyst/ui/quant.jsx` — `TopBar` 加 tab + `WatchMode` 组件 + 蜡烛组件
- `src/financial_analyst/ui/quant.html` — bump `?v=`

**测试:** `tests/test_watch_models.py`、`test_watch_store.py`、`test_watch_triggers.py`、`test_watch_feed.py`、`test_watch_agent.py`、`test_watch_loop.py`、`test_watch_endpoints.py`

**依赖顺序:** T1 models → T2 store → T3 triggers → T4 feed → T5 agent → T6 loop → T7 endpoints → T8 frontend+回归。前 7 个纯后端可独立 TDD;T8 浏览器实测。

---

## Task 1: 数据模型 `watch/models.py`

**Files:** Create `src/financial_analyst/watch/__init__.py`(空)、`src/financial_analyst/watch/models.py`;Test `tests/test_watch_models.py`

- [ ] **Step 1 — 写失败测试** `tests/test_watch_models.py`:
```python
from financial_analyst.watch.models import WatchItem, WatchContext, WatchRec

def test_watchitem_optional_stop():
    it = WatchItem(code="SH600519")
    assert it.stop_loss is None and it.avg_cost is None
    it2 = WatchItem(code="SZ002594", avg_cost=80.0, stop_loss=72.0)
    assert it2.stop_loss == 72.0

def test_watchrec_schema_and_jsonable():
    r = WatchRec(code="SH600519", action="hold", target_price=1800.0,
                 stop_loss=1650.0, reason="放量突破确认", confidence=0.6,
                 trigger_kind="breakout_high", ts="2026-06-02 10:05:00")
    d = r.to_dict()
    assert d["action"] == "hold" and d["trigger_kind"] == "breakout_high"
    assert set(["code","action","target_price","stop_loss","reason","confidence","trigger_kind","ts"]).issubset(d)

def test_watchrec_action_validation():
    import pytest
    with pytest.raises(ValueError):
        WatchRec(code="X", action="moon", reason="", trigger_kind="x", ts="t")
```
- [ ] **Step 2 — 跑红:** `.venv/Scripts/python.exe -m pytest tests/test_watch_models.py -q` → ImportError。
- [ ] **Step 3 — 实现** `models.py`:
```python
from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Dict, Any

_ACTIONS = {"buy", "add", "hold", "reduce", "sell"}  # 与 backtest.decision.DecisionLeg 一致

@dataclass
class WatchItem:
    code: str
    avg_cost: Optional[float] = None     # 用户关注成本 (可选)
    stop_loss: Optional[float] = None    # 用户设的止损 -> 启用 stop_break 触发

@dataclass
class WatchContext:
    code: str
    name: str
    now_ts: str
    trigger: Dict[str, Any]              # {kind, detail, metric}
    realtime: Dict[str, Any]             # Tencent 快照子集
    bars_5min: List[Dict[str, Any]]      # 近 N 根 {datetime,open,high,low,close,vol}
    factors_eod: Dict[str, Any] = field(default_factory=dict)
    news_today: List[str] = field(default_factory=list)
    item: Optional[WatchItem] = None

@dataclass
class WatchRec:
    code: str
    action: str
    reason: str
    trigger_kind: str
    ts: str
    target_price: float = 0.0
    stop_loss: float = 0.0
    confidence: float = 0.0
    error: str = ""
    def __post_init__(self):
        if self.action not in _ACTIONS:
            raise ValueError(f"bad action {self.action!r}, must be {_ACTIONS}")
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
```
- [ ] **Step 4 — 跑绿:** `pytest tests/test_watch_models.py -q` → PASS。
- [ ] **Step 5 — commit:** `feat(watch): WatchItem/WatchContext/WatchRec 数据模型 + 测试`

---

## Task 2: 推荐日志 `watch/store.py`

**Files:** Create `src/financial_analyst/watch/store.py`;Test `tests/test_watch_store.py`

- [ ] **Step 1 — 失败测试**:写一条 `WatchRec` → `append_rec(path, rec)` → 读回断言列齐全;再 `ack_rec(path, ts, code, "confirm")` → 该行 `user_action=="confirm"`。用 `tmp_path` parquet。列:`ts,code,trigger_kind,action,target_price,stop_loss,reason,confidence,user_action,user_action_ts`。
- [ ] **Step 2 — 跑红。**
- [ ] **Step 3 — 实现** `store.py`:`append_rec(parquet_path, rec: WatchRec)`(读现有→concat→去重 key=(ts,code,trigger_kind)→写)、`load_recs(path, day=None)`、`ack_rec(path, ts, code, user_action)`。用 `pandas` + `pyarrow`;`user_action` 默认 `"none"`。路径默认 `get_data_paths().parquet_root / "watch_recommendations.parquet"`(参考 `data/updaters/watchlist.py` 的路径发现)。**单进程 append,不并发写**(沿用项目数据写铁律)。
- [ ] **Step 4 — 跑绿。**
- [ ] **Step 5 — commit:** `feat(watch): 推荐日志 parquet 读写 + ack`

---

## Task 3: 无组合触发适配 `watch/triggers.py`

**Reuse(先读)** `src/financial_analyst/backtest/intraday.py`:`IntradayTrigger`、`IntradayTriggerConfig`、`TriggerEvent`、`Position`(确认 `Position` 字段:`stop_loss`/`qty`/`avg_cost`,以及 `check(code,bars_upto_t,position,sellable_qty,i)` 真实签名)。

**Files:** Create `src/financial_analyst/watch/triggers.py`;Test `tests/test_watch_triggers.py`

- [ ] **Step 1 — 失败测试**:
  - 构造一个上升的 5min DataFrame(末根 high 突破前高 >0.8%)→ `WatchTrigger.check_item(item, bars)` 返回 `kind=="breakout_high"`。
  - `WatchItem(stop_loss=X)` + 末根 low<=X → 返回 `kind=="stop_break"`(无需真实 position)。
  - 无 `stop_loss` 的 item + 破位行情 → **不**触发 stop_break。
  - `news_trigger(code, headlines)` 命中关键词 → 返回 `kind=="news"` 的 TriggerEvent-like。
- [ ] **Step 2 — 跑红。**
- [ ] **Step 3 — 实现** `triggers.py`:
  - `WatchTrigger`(包一个 `IntradayTrigger`):`check_item(item: WatchItem, bars_5min: pd.DataFrame, i: int) -> Optional[TriggerEvent]` —— 用 `item.stop_loss/avg_cost` 造一个轻量 `Position`(无止损则传 `position=None` 让 stop_break 跳过),`sellable_qty=1 if item.stop_loss else 0`,调底层 `IntradayTrigger.check(...)`。
  - `news_trigger(code, headlines: List[str]) -> Optional[TriggerEvent]` —— 命中即合成 `TriggerEvent(kind="news", is_risk=False, detail=headline, metric=0.0, bar_index=-1)`(去重交给 loop)。
  - 复用 `IntradayTriggerConfig` 默认 + `reset_day()` 透传。
- [ ] **Step 4 — 跑绿。** **Step 5 — commit:** `feat(watch): 无组合触发适配 (合成 position + 新闻触发)`

---

## Task 4: 实时数据封装 `watch/feed.py`

**Reuse(先读)** `data/collectors/tencent_quote.py`(`TencentQuoteCollector.fetch`)、`data/updaters/pytdx_kline.py`(`fetch_5min` + `PytdxClient` 构造)。

**Files:** Create `src/financial_analyst/watch/feed.py`;Test `tests/test_watch_feed.py`

- [ ] **Step 1 — 失败测试**(用 monkeypatch/stub,不连网):stub `TencentQuoteCollector.fetch` 返回固定 dict、stub `fetch_5min` 返回构造 bars → `WatchFeed.snapshot(codes)` 返回 `{code:{price,change_pct,vol_ratio,...}}`;`WatchFeed.bars5(code)` 返回 **vol 已转手(÷100)** 的 DataFrame(列 open/high/low/close/vol/trade_date)。
- [ ] **Step 2 — 跑红。**
- [ ] **Step 3 — 实现** `feed.py`:`WatchFeed`:`__init__` 持有一个复用的 `PytdxClient` + `TencentQuoteCollector`;`snapshot(codes)`(一次批量 HTTP);`bars5(code, n=240)`(转成 DataFrame,**vol 股→手 ÷100**,列名对齐 `IntradayTrigger` 期望的 `open/high/low/close/vol/trade_date`);所有网络调用 try/except 失败返回 None/空 + log(容错,不抛)。`NO_PROXY` 由现有 collector 处理(确认)。
- [ ] **Step 4 — 跑绿。** **Step 5 — commit:** `feat(watch): 实时数据封装 (Tencent 快照 + pytdx 5min, vol 归一)`

---

## Task 5: 单股 advisor `watch/agent.py`

**Reuse(先读)** `backtest/decision.py`:LLM client 怎么构造(`DecisionAgent.__init__` 的 `client`)、`DecisionCache` 用法、`build_messages` 的 system/user 结构、JSON 解析方式。**镜像其 client + cache 模式**,但输入是单股 `WatchContext`。

**Files:** Create `src/financial_analyst/watch/agent.py`;Test `tests/test_watch_agent.py`

- [ ] **Step 1 — 失败测试**:注入 mock client(返回固定 JSON,如 `{"action":"add","target_price":1800,"stop_loss":1650,"reason":"...","confidence":0.6}`)→ `await WatchAgent(client=mock).decide_one(ctx)` 返回 `WatchRec(action="add", trigger_kind==ctx.trigger["kind"])`;mock 抛错 → 返回 `WatchRec(action="hold", error!="")`(不崩)。用 `pytest.mark.asyncio` 或 `asyncio.run`。
- [ ] **Step 2 — 跑红。**
- [ ] **Step 3 — 实现** `agent.py`:
  - `class WatchAgent`:`__init__(self, client=None, cache=None, temperature=0.2)`(client 缺省按 decision.py 同法构造)。
  - `_build_messages(ctx: WatchContext) -> list[dict]`:system = advisor 口吻("你是盯盘助手,只针对这一只票给出明确 action(buy/add/hold/reduce/sell)+目标价+止损+理由,理由必须关联触发原因;输出 JSON");user = 把 `ctx`(触发/实时/近 5min/因子技术/新闻/关注成本)格式化。
  - `async def decide_one(ctx) -> WatchRec`:cache key=SHA256(code+now截分钟+trigger.kind+messages);调 client;解析 JSON → `WatchRec`;**任何异常 → `WatchRec(action="hold", error=str(e), ...)`**。
- [ ] **Step 4 — 跑绿。** **Step 5 — commit:** `feat(watch): WatchAgent 单股 advisor (复用 LLM client/cache)`

---

## Task 6: tick 编排 `watch/loop.py`

**Files:** Create `src/financial_analyst/watch/loop.py`;Test `tests/test_watch_loop.py`

- [ ] **Step 1 — 失败测试**(全 stub,不连网/不调真 LLM):
  - 注入 stub `WatchFeed`(给一只票构造 breakout bars)+ stub `WatchAgent`(返回固定 WatchRec)+ 内存 watchlist `["SH600519"]` → `await loop.tick()` → 产出 1 条 recommendation(进 `loop.drain()` 队列)+ 写日志被调用。
  - 同一票同类连续两 tick → 第 2 次被 **cooldown** 挡(无新推荐)。
  - 全局每时段 LLM 调用达上限 → 后续触发不再调 agent。
  - `is_market_open(ts)` 对 10:00 真、12:00 假、周末假。
- [ ] **Step 2 — 跑红。**
- [ ] **Step 3 — 实现** `loop.py`:
  - `WatchLoopConfig`(tick_seconds=60, news_every_n_ticks=5, cooldown_minutes=15, global_llm_cap_per_session=20, trigger_cfg=IntradayTriggerConfig())。
  - `WatchLoop`:持有 `WatchFeed`/`WatchTrigger`/`WatchAgent`/store path + 一个 `asyncio.Queue`(SSE 用)+ cooldown dict `{(code,kind):last_ts}` + 计数。
  - `async tick()`:`is_market_open` 否则 return;读 watchlist(`_load_watchlist_codes`)→ `feed.snapshot`(入队 quote_update)→ 每只 `feed.bars5` → `WatchTrigger.check_item` → 触发且过 cooldown/cap → `await agent.decide_one` → rec 入队(recommendation)+ `store.append_rec` + 记 cooldown/计数。新闻每 N tick 轮询(命中合成 news 触发)。
  - `is_market_open(ts)`:9:30–11:30 / 13:00–15:00 + 交易日(简单版:周一~周五;可后续接 research calendar)。
  - `async run()`:while 自循环 `await asyncio.sleep(tick_seconds)`;`stop()` 置标志。`drain()` 取队列。**单 tick 内任一票异常被捕获不影响其它票**。
- [ ] **Step 4 — 跑绿。** **Step 5 — commit:** `feat(watch): WatchLoop tick 编排 + cooldown/cap + 市场时段`

---

## Task 7: buddy `/watch/*` 端点 + 后台 task

**Reuse(先读)** `buddy/server.py`:`app` 对象名、`/backtest/run` 后台 task 范式、`/run` 的 `StreamingResponse` SSE 写法、`_safe_json_dumps`、`_jsonable`。

**Files:** Modify `src/financial_analyst/buddy/server.py`;Test `tests/test_watch_endpoints.py`

- [ ] **Step 1 — 失败测试**(httpx ASGI + stub WatchLoop,不连网):
  - `POST /watch/start` → `{status:"started"}`;`GET /watch/status` → `running:true`。
  - 订阅 `GET /watch/stream`(SSE),stub loop 注入一条 rec → 收到 `event: recommendation`。
  - `POST /watch/ack {ts,code,user_action:"confirm"}` → 日志该行 user_action=confirm。
  - `POST /watch/item {code,stop_loss}` → watch item 生效;`/watch/stop` → `running:false`。
- [ ] **Step 2 — 跑红。**
- [ ] **Step 3 — 实现**(改 `buddy/server.py`,镜像现有范式):
  - 模块级单例 `_watch_loop: Optional[WatchLoop]`(stub 可注入)。
  - `POST /watch/start`:建 `WatchLoop` + `asyncio.create_task(loop.run())`;`/watch/stop`:`loop.stop()`。
  - `GET /watch/stream`:`StreamingResponse(gen(), media_type="text/event-stream")`,`gen` 从 `loop.queue` `await get()` 持续 yield `_safe_json_dumps`(事件 quote_update/trigger/recommendation/heartbeat);客户端断开优雅退出。
  - `POST /watch/ack` → `store.ack_rec`;`POST /watch/item` → 更新内存 watch items;`GET /watch/status` → running + counts。
  - 所有响应走 `_jsonable`。**不阻塞事件循环**(数据/LLM 在 loop 的 task 里,必要时 `asyncio.to_thread`)。
- [ ] **Step 4 — 跑绿** + `pytest tests/ -q`(确认没破坏现有 buddy 测试)。
- [ ] **Step 5 — commit:** `feat(watch): buddy /watch SSE + 控制端点 + 后台 loop task`

---

## Task 8: 前端「盯盘」页 + 回归

**Reuse(先读)** `ui/quant.jsx`:`QuantApp`/`TopBar`/`mode`/已有 chart 组件;`quant.html` 的 `?v=`。

**Files:** Modify `src/financial_analyst/ui/quant.jsx`、`src/financial_analyst/ui/quant.html`

- [ ] **Step 1 — TopBar tab:** `TopBar.tabs` 加 `{ k:'watch', l:'实时盯盘' }`;`QuantApp` 加 `{mode==='watch' && <WatchMode/>}`。
- [ ] **Step 2 — `WatchMode` 组件**(3 栏):
  - 左:自选列表,`EventSource(GUANLAN_BACKEND+'/watch/stream')` 收 `quote_update` 驱动实时价/涨跌/⚡;点选 `setSel(code)`。
  - 中:`<Candle code={sel}/>`。
  - 右:recommendation feed(收 `recommendation` 事件 unshift 到 list),每条 `[确认][忽略]` → `fetch POST /watch/ack`。
  - 顶部 `[开始盯盘]/[停止]` → POST `/watch/start`·`/watch/stop`;`GET /watch/status` 初始化。
- [ ] **Step 3 — `Candle` 蜡烛组件**(新,SVG):入参 `{code}`,初次拉一段 5min(可加 `GET /watch/bars?code=` 端点或复用现有数据端点),画蜡烛 + MA。**最小版:先静态渲染一段 bars,确保不白屏**;实时增量可 v1.1。
- [ ] **Step 4 — bump** `quant.html` 的 `quant.jsx?v=` → `?v=20260602-1`。
- [ ] **Step 5 — babel 编译校验:** `node` + `@babel/standalone` 编译 `quant.jsx`(沿用项目既有校验方式)→ 无语法错。
- [ ] **Step 6 — 浏览器实测**(Claude Preview):起 `fa serve`(:9999)+ http.server 托管 ui → 打开 quant.html → 点「实时盯盘」tab → 3 栏渲染、SSE 连上(stub/真)、收到一条 recommendation 显示 + 确认按钮可点、现有 5 tab 不白屏。
- [ ] **Step 7 — 全量回归:** `.venv/Scripts/python.exe -m pytest tests/ -q` → `1180 + 新增 watch 测试 passed / 3 skipped`。
- [ ] **Step 8 — commit:** `feat(watch): quant 工作台「实时盯盘」页 (3 栏 + 蜡烛 + SSE feed) + 回归绿`

---

## 收尾(全部任务后)

- [ ] 最终 code review(dispatch reviewer)。
- [ ] 追加 `research/strategy/log.md` 一条(infra/feat)。
- [ ] PROJECT.md 若需提及盯盘功能则更新。
- [ ] **不 push、不合 main**(私有红线);留在 merge-stocks。
- [ ] 真 LLM + 真行情的盘中实测在有 key + 交易时段的本机做(CI 无则 skip,仿 backtest 的 real-data smoke)。

## Self-review notes(写计划时已查)
- **Spec 覆盖:** §3 数据流→T6 loop;§4 复用→T3/T4/T5/T7;§5 WatchAgent→T5;§6 触发适配→T3;§7 后端→T6/T7;§8 前端→T8;§9 持久化→T2;§10 容错→各 task try/except;§11 测试→每 task TDD + T8 回归。全覆盖。
- **类型一致:** `WatchRec.action` 用与 `backtest.DecisionLeg` 同的 5 值集合;`bars` 列名(open/high/low/close/vol/trade_date)T4 产出 = T3 `IntradayTrigger` 期望。
- **无 placeholder:** 集成点(decision.py/intraday.py/buddy/quant.jsx)给了"先读哪个文件镜像什么"的精确指令而非 TODO;新逻辑给了真实代码。
- **风险:** vol 单位(T4 归一)、新闻去重(T6 cooldown/dedup)、LLM 延迟(T8 feed 先显示"研判中")。
