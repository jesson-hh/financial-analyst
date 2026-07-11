# 落子页改造(三页重排 + 后端盯盘 + 删减)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把落子页从「三轴八面 + 启发式/记账空转」重排为「今日 | 复盘 | 策略」三页任务流,盯盘升级为后端定时研判,删除 scanSeat 启发式全家与台账记账半边。

**Architecture:** 前端 6 个 babel-jsx(window 全局互通)做外科手术:app 壳换单轴 page 路由,fleet 重写为票池列表,panels 三卡合一+瘦身,foundry 去演武;后端新增 `seats/watcher.py`(asyncio 任务,进程内直调 decide 内核,严禁 HTTP 自调)+ 2 端点。后端既有 20 端点零破坏。

**Tech Stack:** FastAPI + 原生 React18(babel-standalone,无构建)+ pytest。

**Spec:** `docs/superpowers/specs/2026-07-11-luozi-renovation-design.md`(四项用户决策已确认)。

## Global Constraints

- 分支:从**最新 main**(已含变体重训 a60a35f)切 `feat/luozi-renovation`;**只 `git add` 本计划涉及的路径,严禁 `git add -A`**(工作树可能残留并发会话文件,一律不碰)。
- 改任何 jsx 必 bump `ui/seats/观澜 · 落子.html` 对应 `?v=`(仓规)。
- 「真·LLM」徽章只挂真 decide 落盘;无真值显「—」/诚实空态,绝不冒充。
- watcher 内严禁在事件循环里同步自 HTTP / 阻塞调用(LLM 调用走 `asyncio.to_thread`)。
- 视觉沿用现有中式 tokens,不引入新设计体系。
- 后端 20 既有端点行为不变(pytest 既有套件全绿为证);`/seats/ledger*`、`/seats/calibration`、`/seats/basket_perf` 保留(帷幄在用)。
- 改后端需重启 9999 才生效(用 9998 版验证亦可,守护由 check_9999.ps1 负责)。

---

### Task 1: 后端定时盯盘 watcher(TDD,可与前端并行)

**Files:**
- Create: `guanlan_v2/seats/watcher.py`
- Create: `tests/test_seats_watcher.py`
- Modify: `guanlan_v2/seats/api.py`(文件尾追加 2 路由 + decide 路由体提取 `_decide_impl`,行为不变)
- Modify: `guanlan_v2/server.py`(startup 挂 env 门控任务)

**Interfaces(Produces):**
```python
# guanlan_v2/seats/watcher.py
DEFAULT_BUDGET = 24
STATE_PATH = VAR / "seats_watch.json"   # {"enabled": bool, "daily_budget": int, "counts": {"2026-07-10": 2}}
def load_state() -> dict            # 缺文件→ {"enabled": False, "daily_budget": 24, "counts": {}}
def save_state(st: dict) -> None
def set_enabled(on: bool) -> dict   # 改 enabled 落盘,返回 get_status()
def watching_codes() -> list[dict]  # 读 var/archive/strat_*.json,bind 非空并集:[{code, strategy_id, name, clock, creed, w, pa, pa_method, refs}]
def get_status() -> dict            # {enabled, watching:[codes], today_count, daily_budget, last_tick, market_open}
def _is_market_open(now) -> bool    # 交易日(引擎日历,失败回退周一~五)+ 09:30-11:30/13:00-15:00
def _throttle_ok(code, freq, last_ts, now) -> bool  # 10min 地板 + hourly≥1h + daily 当日一次
def tick(now=None, decide_fn=None, quote_fn=None, decisions_tail_fn=None) -> dict
    # 全可注入(测试用桩):enabled+盘中+预算余 → 逐 watching code:
    #   quote_fn(code)['fresh'] 为真 → 节流过 → decide_fn(payload) → counts+1
    # payload 含 code/date=now/seat_cn=策略名/creed/recipe(refs 解析)/w/pa/pa_method/
    #   industry=引擎股票元数据真值(取不到传 '' 但不得硬编码)/source='watcher'
    # 返回 {"judged": [codes], "skipped": {code: reason}}
async def run_loop(interval_s: int = 300)   # while True: enabled→ await asyncio.to_thread(tick); await asyncio.sleep
```
- 真 decide:生产路径 `decide_fn=None` 时用 api.py 提取出的 `_decide_impl(payload: dict) -> dict`(POST /seats/decide 路由改薄壳调它,行为不变由既有 decide 测试守护);`source:'watcher'` 随 `_persist_decision` 落盘。
- `decisions_tail_fn` 生产实现 = 读 `var/seats_decisions.jsonl` 尾部按 code 取最新 ts;测试注入。

**Steps:**
- [ ] 1. 写失败测试 `tests/test_seats_watcher.py`(全桩,零网络零 LLM):
```python
import json
from datetime import datetime
from guanlan_v2.seats import watcher

def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    st = watcher.load_state()
    assert st == {"enabled": False, "daily_budget": watcher.DEFAULT_BUDGET, "counts": {}}
    st["enabled"] = True; watcher.save_state(st)
    assert watcher.load_state()["enabled"] is True

def test_market_open_gate():
    assert watcher._is_market_open(datetime(2026, 7, 10, 10, 0)) is True    # 周五盘中
    assert watcher._is_market_open(datetime(2026, 7, 10, 12, 0)) is False   # 午休
    assert watcher._is_market_open(datetime(2026, 7, 11, 10, 0)) is False   # 周六
    assert watcher._is_market_open(datetime(2026, 7, 10, 15, 30)) is False  # 收盘后

def test_throttle_floor_and_freq():
    now = datetime(2026, 7, 10, 10, 30)
    assert watcher._throttle_ok("300750", "hourly", None, now) is True
    assert watcher._throttle_ok("300750", "hourly", "2026-07-10T10:25:00", now) is False  # <10min 地板
    assert watcher._throttle_ok("300750", "hourly", "2026-07-10T09:50:00", now) is False  # <1h
    assert watcher._throttle_ok("300750", "hourly", "2026-07-10T09:25:00", now) is True
    assert watcher._throttle_ok("300750", "daily", "2026-07-10T09:35:00", now) is False   # 当日已判
    assert watcher._throttle_ok("300750", "daily", "2026-07-09T14:00:00", now) is True

def test_tick_budget_and_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    watcher.save_state({"enabled": True, "daily_budget": 2, "counts": {}})
    calls = []
    codes = [{"code": c, "strategy_id": "s1", "name": "动量 · 默认",
              "clock": {"decisionFreq": "hourly"}, "creed": "x", "w": 0, "pa": False,
              "pa_method": "", "refs": []} for c in ("300750", "600519", "000001")]
    monkeypatch.setattr(watcher, "watching_codes", lambda: codes)
    out = watcher.tick(now=datetime(2026, 7, 10, 10, 0),
                       decide_fn=lambda p: calls.append(p) or {"ok": True},
                       quote_fn=lambda c: {"fresh": True},
                       decisions_tail_fn=lambda c: None)
    assert out["judged"] == ["300750", "600519"] and len(calls) == 2      # 预算 2 截断
    assert watcher.load_state()["counts"]["2026-07-10"] == 2
    assert calls[0]["source"] == "watcher" and calls[0]["code"] == "300750"
    out2 = watcher.tick(now=datetime(2026, 7, 10, 10, 0), decide_fn=lambda p: {"ok": True},
                        quote_fn=lambda c: {"fresh": True}, decisions_tail_fn=lambda c: None)
    assert out2["judged"] == [] and "budget" in str(out2["skipped"])       # 预算耗尽

def test_tick_skips_stale_quote_and_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(watcher, "STATE_PATH", tmp_path / "w.json")
    watcher.save_state({"enabled": True, "daily_budget": 9, "counts": {}})
    monkeypatch.setattr(watcher, "watching_codes", lambda: [{"code": "300750", "strategy_id": "s1",
        "name": "n", "clock": {}, "creed": "", "w": 0, "pa": False, "pa_method": "", "refs": []}])
    out = watcher.tick(now=datetime(2026, 7, 10, 10, 0), decide_fn=lambda p: {"ok": True},
                       quote_fn=lambda c: {"fresh": False}, decisions_tail_fn=lambda c: None)
    assert out["judged"] == [] and out["skipped"]["300750"] == "stale_quote"
    watcher.save_state({"enabled": False, "daily_budget": 9, "counts": {}})
    out2 = watcher.tick(now=datetime(2026, 7, 10, 10, 0), decide_fn=lambda p: {"ok": True},
                        quote_fn=lambda c: {"fresh": True}, decisions_tail_fn=lambda c: None)
    assert out2 == {"judged": [], "skipped": {"_": "disabled"}}
```
- [ ] 2. `python -m pytest tests/test_seats_watcher.py -x -q` → 全 FAIL(模块不存在)。
- [ ] 3. 实现 `watcher.py` 使其全过(纯函数分层如上;`watching_codes` 读 `var/archive/strat_*.json`,坏 json 跳过;日历 `try: 引擎 loader._load_calendar('day') except: 周一~五`)。
- [ ] 4. api.py 尾部加路由;server.py startup 挂 env 门:
```python
# api.py 追加
@router.get("/watch/status")
def watch_status():
    from . import watcher
    return {"ok": True, **watcher.get_status()}

@router.post("/watch/toggle")
def watch_toggle(payload: dict = Body(...)):
    from . import watcher
    return {"ok": True, **watcher.set_enabled(bool(payload.get("on")))}

# server.py startup 内
if os.environ.get("GUANLAN_SEATS_WATCH") == "1":
    from guanlan_v2.seats import watcher as _seats_watcher
    asyncio.create_task(_seats_watcher.run_loop())
```
- [ ] 5. 提取 `_decide_impl`:decide 路由体重构为「解析 → `_decide_impl(payload)`」,`pytest tests/ -q -k "decide"` 既有测试守护零变;watcher 生产 `decide_fn=_decide_impl`。
- [ ] 6. `python -m pytest tests/ -x -q -k "seats or watcher"` 全绿;`git add guanlan_v2/seats/watcher.py guanlan_v2/seats/api.py guanlan_v2/server.py tests/test_seats_watcher.py`,commit `feat(seats): 后端定时盯盘 watcher——盘中节拍研判绑定票,日预算+状态文件,decide 内核直调`。

### Task 2: 数据层手术(luozi-data.jsx)

**Files:** Modify `ui/seats/luozi-data.jsx`;Modify `ui/seats/观澜 · 落子.html`(bump `?v=`)

**Interfaces(Produces,供 Task 4-5 消费):**
```js
lzFetchWatchStatus() -> Promise<{enabled,watching,today_count,daily_budget,market_open}|null>
lzToggleWatch(on) -> Promise<same|null>
lzWatchSet(code, on) -> void      // on: 绑进本票当前策略(无则「动量 · 默认」);off: 从所有策略 bind 移除;经 strategySave 写 GL
lzFetchDecisionsTimeline(code, limit=60) -> Promise<rows[]|null>   // GET /seats/decisions?code=&limit=&exclude_runs=1
```

**Steps:**
- [ ] 1. 删除:`shadow*` 全家(:1548-1671 + 导出)、`consensusEquity/lzConsensusEquity`、mulberry32 `benchmark()`、死导出 `lzRegimeAt/lzRsiOf/lzBuildTriggerCtx/lzSeedDefaultStrategy/lzPaFeatures/lzRenderPaNote`(`paFeatures/renderPaNote` 本体保留——后端镜像契约)、`evidenceFor` 与 `regimeAt`、`scanSeat/seatEquity`(触发引擎 `rsiOf/buildTriggerCtx/evalTrigger/runTriggerReplay` **保留**,条件单在用)、`SEATS_ALL` 收敛 momentum 单席常量(seatColor 回退)。`buildSymbolFromBars` 去 decisions/perSeat 产出,保 bars/bench/meta。
- [ ] 2. 新增上表 4 函数 + 导出;`monitoredCodes/monitorAgentFor/poolIsMonitored` 保留(watcher 同源语义)。
- [ ] 3. 浏览器冒烟(新 `?v=`):console 0 错;`lzWatchSet('600519', true)` 后「动量 · 默认」bind 含 600519,`false` 移除。
- [ ] 4. `git add ui/seats/luozi-data.jsx "ui/seats/观澜 · 落子.html"`,commit `refactor(luozi): 数据层删启发式/影子/死导出,加盯盘状态与一键绑定`。

### Task 3: 图表层(luozi-chart.jsx)

- [ ] 删 CandleChart `decisions` prop 及启发式标记层/持仓底色层(chart:68-79,112-118,180-208)、`css()`(chart:19)、图例「淡=启发式预览」句;truemarks/triggers/news/forming/asof 墙保留;EquityChart 不动。bump `?v=`,commit `refactor(luozi): K线删启发式死层`。

### Task 4: 面板层(luozi-panels.jsx)

**新/改组件契约:**
```js
JudgeCard({code, strat, onDecided})              // 研判卡:「▶ 研判一次」快/深 + 时间线(lzFetchDecisionsTimeline)+ 行展开 EvidenceFields
EvidenceFields({dec})                            // 从 RunDecCard 抽出的证据链渲染(vintage IC/混合/叙事/regime/PA/思维链),RunDecCard 与 JudgeCard 共用
DecisionTrail({code})                            // 决策留痕:按日分组只读时间线(研判/条件单/触发)
OrderWatchPanel                                  // 瘦身:删 loopOn/runTimedDecide/影子行/w 滑杆/onRealDecide/自动落账;保 立单(day|5min)/验触发/8s 实时比对/onTrigger(仅标图+提示)
MetricsStrip({repPerf, benchTotal, benchAsof})   // 只留回测分支;删台账/影子/死 props;StockHero 独立导出供今日页
```
- [ ] 1. 实施:删 LedgerPanel/TCA 卡/LiveDecideFlow(并入 JudgeCard)/DecisionCard(启发式分支、`ev.*` 消费、signal_pack 触发因子区全删;「席位·agent 研判」区并入 JudgeCard;DecisionHistory 抽屉保留供 JudgeCard「⏱ 全部」);RunDecCard 改用 EvidenceFields;JudgeCard 手动研判 payload `industry` 用 `LZ_SYMBOLS[code].meta.industry`(修恒空 bug)。
- [ ] 2. bump `?v=`;浏览器冒烟 0 console error;commit `refactor(luozi): 三决策卡合一+台账瘦身为决策留痕+条件单卡去循环`。

### Task 5: 主壳三页路由(luozi-app.jsx)+ 票池列表(luozi-fleet.jsx)

**app 新状态轴:** `page: 'today'|'replay'|'strategy'`(默认 today);退役 `mode/view/workspace/playing/speed/cursor/markerReveal/thinking/fleetWatch/monQuotes/shadow/ledger*`;保留 `code/tf/strategies/curStratId/selected/selRun/runDecs/realRun/runsBump/quote/book/ticks/news*/liveBar/orderTriggers/market/zoom/panEnd/dark/toast`。
- **today**:左 `TicketList`(重写 luozi-fleet.jsx:行 = 名/码/现价/涨跌%(7s 轮询池内票)+ 最新研判徽章 + 盯盘点开关 lzWatchSet;点行 setCode)+ 中 StockHero+CandleChart(6s quote、forming 柱、新闻泳道 live)+ 右 JudgeCard/OrderWatchPanel/DecisionTrail/OrderbookTicksPanel;顶部 MarketBar + 盯盘总闸(30s lzFetchWatchStatus,lzToggleWatch;未启用/盘外显诚实态)。
- **replay**:顶部向导(票 select 池内票 / 策略 select / 起止 date 默认近 120 交易日 / 粒度 日|30分 →「开始真跑」);`runRealThink({code,stratId,startDate,endDate,freq})` 重写:idx 区间由日期二分,**与游标解耦**;进度沿用 realRun 态;完成自动选中新 run。下方 RunPicker + MetricsStrip(回测)+ CandleChart(标记 = runDecs 全显;news asof = 选中决策日 || run end)+ EquityChart 双线 + RunDecCard。删 PlaybackBar/Deliberation/实盘自动选中 effect/揭示墙播放逻辑。
- **strategy**:渲染 Foundry(Task 6 瘦身版)。
- 顶栏:三页签 + 主题按钮(Tweaks 收缩);股票下拉/移出池 pill 移入 TicketList;cockpit 交棒保留(入池+聚焦+通知条,交棒强制 page='today')。
- [ ] 1. 实施;两文件 bump `?v=`。
- [ ] 2. 真机三页文本核 + 0 console error;commit `feat(luozi): 三页任务流重排——今日/复盘/策略,复盘向导化与游标解耦`。

### Task 6: 校场瘦身(luozi-foundry.jsx)

- [ ] 删 `strategyArena/runArena/recommend/board Sharpe 序/「荐」徽章/待演武文案`(foundry:34-72,127-146,395-399);列表按名称序;编辑器/料库/回收站不动;副题改「遣 经验卡 · 因子 · 研报,自组命名策略;验证去『复盘』页真跑」。bump `?v=`;commit `refactor(luozi): 校场去演武——验证归复盘真跑`。

### Task 7: 烂尾清理 + 帷幄文案

- [ ] 1. 删 `var/archive/strat_mqae2q6f2th.json`(0612演习策略)、`var/archive/strat_mqf7alg41jz.json`(铜陵有色 · 盯盘);luozi-data.jsx 启动迁移 `_pruneRetiredStrategies()`:GL 按这两个 id remove(幂等)。
- [ ] 2. `guanlan_v2/console/tools.py` `seats_bind_impl` 文案:「页面开着时由前端盯盘循环持续研判…非服务器 7×24」→「已绑定;服务端盯盘(GUANLAN_SEATS_WATCH=1)盘中按策略节拍自动研判,结果入研判历史;未开启时可在落子页手动研判」;同步 `tests/test_console_tools.py` 断言。
- [ ] 3. `pytest tests/test_console_tools.py tests/test_guanlan_mcp_server.py -q` 绿;commit `chore(luozi): 清烂尾策略实体 + ww_seats_bind 文案对齐后端盯盘`。

### Task 8: 全量回归 + 真机验收

- [ ] 1. `python -m pytest tests/ -q` 全绿(基线 1142,新增 watcher)。
- [ ] 2. 重启 9999 拉新后端;浏览器:今日页(票池/盯盘开关写 GL/研判一次/条件单/盘口)、复盘页(向导短区间日线真跑 → run 自动选中 → 净值/标记/流水/证据)、策略页(编辑保存/料库/无演武残留);三页 0 console error。
- [ ] 3. `GET /seats/watch/status` 真机可读;`POST /seats/watch/toggle` 开→状态文件落盘→关(周六盘外,零 LLM 触发)。
- [ ] 4. 选股页「据此落子」交棒回归(入池+聚焦+通知条)。
- [ ] 5. `ui/seats/README.md` 头部加「2026-07-11 三页重排」段(编年史保留);散修汇总 commit。

## Self-Review

- Spec 覆盖:三页架构(T5)/后端盯盘(T1)/台账→留痕(T4)/启发式全删(T2/T3/T4/T5/T6)/烂尾清理与 ww 文案(T7)/验收六条(T8)——齐。
- 类型一致:`lzWatchSet/lzFetchWatchStatus/lzToggleWatch/lzFetchDecisionsTimeline` T2 定义、T4/T5 消费;`EvidenceFields` T4 定义共用;watcher `tick` 注入签名与测试逐字一致。
- 无 TBD;JSX 大手术以「删除锚点(行号)+ 组件契约」表述,执行者=主会话 agent(持四路测绘全上下文);后端为可注入纯函数 TDD。
