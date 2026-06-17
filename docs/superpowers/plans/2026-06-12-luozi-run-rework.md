# 落子页重排:回测历史 run 化 + 单 agent + 实盘仓位台账 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落子页复盘模式以「回测历史(run)」为第一公民(每次 agent 真跑=一个可回看的 run,右栏选 run→流水+K线 B/S 标记跟随切换),撤多席合议改单 agent 视图,实盘改初始资金+逐日仓位台账(今日置顶+往日折叠);舰队保留但单 agent 口径。

**Architecture:** 后端两本新账(`var/seats_runs.jsonl` run 头、`var/seats_ledger.jsonl` 台账事件流)+ decide 落盘加 run_id;前端 runRealThink run 化、右栏 RunPicker/LedgerPanel、左栏「当前策略」单卡。scanSeat/buildSymbolFromBars/seatEquity **签名与产物不动**(校场演武共用);演武不落盘天然不入回测历史。

**Tech Stack:** FastAPI + jsonl append-only / no-build React UMD jsx(`ui/seats/`)/ pytest

**硬约束(全程):**
- 本仓无 git,「提交」=跑 pytest(基线 **204 绿**,口径 `& "G:\financial-analyst\.venv\Scripts\python.exe" -m pytest tests -q --ignore=tests/test_recipe_memory.py --ignore=tests/test_session_seed.py`)
- 改 python 须重启 9999(杀监听 PID,看门狗 ~10s 拉新);改 jsx 必 bump `观澜 · 落子.html` 的 `?v=`(用 Edit 非 sed);G:/stocks 只读
- UI 视觉语言(文人书案)只重排不重建;`/seats/decide` 直调必传 date;落盘 code=normalize 后 SH/SZ 前缀,前端按「数字核」匹配
- 历史落盘记录绝不改写;LLM 失败不落盘(run 内 errors 只计数)

**勘察锚点(行号以 2026-06-12 勘察为准):**
- 前端:`luozi-app.jsx`(19-56 state / 168-219 runRealThink / 221-232 onLiveDecide / 496 SeatRail / 512 CandleChart / 527-543 右栏+PlaybackBar)、`luozi-panels.jsx`(42-336 OrderWatchPanel / 469-532 MetricsStrip / 567-597 SeatRail / 603-643 DecisionFlow / 655-663 ReasoningChain / 665-735 DecisionHistory / 736-1001 DecisionCard)、`luozi-data.jsx`(348-357 consensusEquity / 978-992 seatDecide / 1119-1242 shadow 八件套 / 1244-1273 window 导出)、`luozi-fleet.jsx`(41-99 FleetCard / 101-121 FleetGrid)
- 后端:`guanlan_v2/seats/api.py`(166-177 _persist_decision / 179-202 GET /decisions / 204-258 calibration / 475-718 POST /decide)
- 帷幄哨兵轮询:`ui/console/console-data.jsx:149-155`(8s 轮询 /seats/decisions?limit=20)
- 关键 shape:run 决策(realDec)`{key,seat,idx,date,side,direction,conf,rationale,reasoning,asof,model_name}` **无 ev/price/size**,与 scanSeat 决策不兼容 → run 选中详情走新 RunDecCard,不进旧 DecisionCard

---

## Task 1: 后端 run 化(decide 透传 + runs 注册/列表 + decisions 过滤 + 校准隔离)— TDD

**Files:**
- Modify: `guanlan_v2/seats/api.py`
- Test: `tests/test_seats_runs.py`(新建)

- [ ] **Step 1 失败测试** `tests/test_seats_runs.py`(TestClient 直挂 build_seats_router,参照 tests/ 现有 seats 测试的装配方式;monkeypatch 落盘路径到 tmp_path):
  1. `test_decide_persists_run_id`:POST /seats/decide 带 `run_id:'run_x1'`(monkeypatch LLM 调用返回固定成功)→ 读 seats_decisions.jsonl 末行含 `run_id=='run_x1'`;不带 run_id → 落盘记录无 run_id 键(钉死此口径)
  2. `test_runs_register_and_list`:POST /seats/runs `{run_id,code:'SH688012',strategy_id,strategy_name,tf:'D',start_date,end_date,n_buy,n_sell,n_watch,n_err,model}` → 200;GET /seats/runs?code=688012(数字核)→ 列表含该 run,逆序
  3. `test_decisions_filter_run_id`:落 3 条(2 条 run_a、1 条无)→ GET /seats/decisions?run_id=run_a 恰 2 条;GET /seats/decisions(无参)默认含全部(向后兼容);GET /seats/decisions?exclude_runs=1 恰 1 条
  4. `test_calibration_excludes_runs`:构造带 run_id 与不带的 decide 记录,calibration 的记录收集只吃不带 run_id 的(防 PIT 回放灌爆命中率样本)
- [ ] **Step 2 跑测试确认失败**
- [ ] **Step 3 实现** `seats/api.py`:
  (a) decide(:475-718)payload 取 `run_id = str(payload.get('run_id') or '').strip()`,_persist_decision 调用点(:695-706)rec 加 `**({'run_id': run_id} if run_id else {})`
  (b) `GET /seats/decisions`(:179-202)加可选 Query `run_id: Optional[str]=None, exclude_runs: int=0`:run_id 给定→只留 `rec.get('run_id')==run_id`;exclude_runs→剔除有 run_id 的行
  (c) 新 `POST /seats/runs`:校验 run_id/code 非空,append `var/seats_runs.jsonl`(_persist 同款 append-only,自动 ts/id);新 `GET /seats/runs`(Query code 可选,数字核匹配=两边 `re.sub(r'\D','',code)` 比对,limit 默认 30 cap 100,逆序)
  (d) calibration(:204-258)记录收集处剔除 `rec.get('run_id')`
- [ ] **Step 4 测试绿 + 全量回归**(204+4)
- [ ] **Step 5 重启 9999 冒烟**:curl POST /seats/runs + GET /seats/runs 真机一笔

## Task 2: 后端实盘台账(事件流 + 状态重放)— TDD

**Files:**
- Modify: `guanlan_v2/seats/api.py`
- Test: `tests/test_seats_ledger.py`(新建)

**口径(已与用户对齐):全局一本账(实盘=一个组合),后端持久。** 事件流 append-only `var/seats_ledger.jsonl`:
- `{kind:'open', date, cash:初始资金}`(开账;再次 open=重置开新账,旧事件留档,state 从最后一个 open 起算)
- `{kind:'trade', date, code, name, side:'buy'|'sell', price, qty, reason, source:'manual'|'order'|'decide', decision_id?}`(服务端校验:buy 现金够、sell 持仓够,加权平均成本;qty 股数)
- `{kind:'decision', date, code, name, direction, confidence, decision_id, source:'timer'|'manual'|'sentry'}`(纯决策记录,不动仓位)

- [ ] **Step 1 失败测试**:
  1. `test_ledger_open_and_state`:POST open(cash=100000, date)→ GET /seats/ledger/state → `{ok, opened:true, start_date, cash:100000, positions:[], days:[]}`
  2. `test_ledger_trade_flow`:open→buy 100 股@50(cash 95000, positions[{code,qty:100,avg_cost:50}])→sell 60@55(cash+3300, qty 40)→ 卖超持仓 → 422;现金不够 buy → 422
  3. `test_ledger_days_grouping`:两日各落 trade+decision → state.days 按日逆序分组,每日 `{date, trades:[], decisions:[]}`
  4. `test_ledger_equity_mtm`:monkeypatch loader 日线收盘 → equity = cash + Σqty×close(无价票诚实 equity=null 并标 covered 计数)
- [ ] **Step 2 确认失败**
- [ ] **Step 3 实现**:`POST /seats/ledger`(body.kind 分派校验后 append)+ `GET /seats/ledger/state`(重放最后一个 open 之后的事件 → cash/positions(avg_cost)/days 按日逆序分组;equity:loader 取各持仓票 ≤today 最新收盘 MTM;异常诚实降级 equity=null)
- [ ] **Step 4 测试绿 + 全量回归**
- [ ] **Step 5 重启 9999 真机**:open→trade→state 三连 curl

## Task 3: 前端数据层(run/台账封装 + runRealThink run 化)

**Files:**
- Modify: `ui/seats/luozi-data.jsx`(window 导出区 :1244-1273 前加函数)
- Modify: `ui/seats/luozi-app.jsx`(runRealThink :168-219)

- [ ] **Step 1** luozi-data.jsx 新增并导出(全部诚实降级返 null/[]):
  - `lzRunsList(code)` → GET /seats/runs?code=(传数字核)
  - `lzRunDecisions(runId)` → GET /seats/decisions?run_id=&limit=300,返回数组
  - `lzLedgerState()` / `lzLedgerPost(ev)` → 台账两端点封装
  - `lzRunId()` → `'run_'+Date.now().toString(36)+Math.random().toString(36).slice(2,6)`
- [ ] **Step 2** runRealThink run 化(luozi-app.jsx:168-219):开跑生成 `const runId = lzRunId()`;`lzSeatDecide` payload 加 `run_id: runId`;循环计数 nBuy/nSell/nWatch/nErr;循环结束(含中途停)POST /seats/runs 注册 run 头 `{run_id, code(数字核), name, strategy_id, strategy_name, tf:'D', start_date:首笔date, end_date:末笔date, n_buy, n_sell, n_watch, n_err, model:末笔model_name}`;注册成功后 `setRunsBump(x=>x+1)` 触发 RunPicker 刷新
- [ ] **Step 3** 浏览器冒烟:控制台直调 window.lzRunsList/lzRunDecisions 返回真数据(bump ?v 先行)

## Task 4: 复盘右栏重排(RunPicker 第一位 + 流水/K线跟随 + RunDecCard)

**Files:**
- Modify: `ui/seats/luozi-panels.jsx`(新组件 RunPicker、RunDecCard;DecisionFlow 双源)
- Modify: `ui/seats/luozi-app.jsx`(右栏装配 :527-543、truedecs :512、新 state)

**数据流设计:** app 持 `selRun`(选中 run 对象)与 `runDecs`(该 run 决策,**date→idx 已映射**)。映射:决策 PIT 日(落盘 asof 的 YYYY-MM-DD 前缀)在 bars 里 findIndex;找不到的标 `offChart:true` 仍进流水但不上图。K 线:`truedecs = selRun ? runDecs : (realDecs[code]||[])`;**选中 run 时 fdecs(scanSeat 骨架标记)传空数组**——只看这个 agent 的判断,防混淆。

- [ ] **Step 1** RunPicker 组件(panels):props `{code, bump, selRun, onSelect}`;useEffect 按 (code,bump) 拉 lzRunsList;列表项:`MM-DD HH:mm · 策略名 · 日期窗 · 买x卖y观z` + model 小字;点选→onSelect(run),再点同条=取消;空态「尚无回测历史——点下方『让 agent 真跑』生成第一次」;视觉沿用右栏面板头部样式,朱砂高亮选中
- [ ] **Step 2** app 接线:`selRun/runDecs/runsBump` 三 state;选 run effect:lzRunDecisions→date→idx 映射→setRunDecs;**切 code/mode 清 selRun**(:235-239 复位组);右栏复盘顺序:RunPicker → DecisionFlow → OrderWatchPanel(实盘不变);CandleChart(:512)`truedecs={tf==='D' ? (selRun? runDecs : (realDecs[code]||[])) : []}` `decisions={selRun? [] : dispFrame.fdecs}`
- [ ] **Step 3** DecisionFlow 双源(panels:603-643):props 加 `runDecs, selRun`;selRun 时列表=runDecs 逆序(行:方向徽章 买入朱砂/卖出黛绿/观望灰 + date + conf + rationale 截断 + offChart 标),点选传 `{...rec, _isRun:true}`;标题「决策流水 · {策略名}」+计数
- [ ] **Step 4** RunDecCard(panels 新组件):selected._isRun 时替 DecisionCard 渲染:direction/confidence/asof/model_name 头 + rationale 正文 + key_evidence + recipe_factors + ReasoningChain(:655 复用);**不读 ev/price/size**;app:538-541 `selected._isRun ? <RunDecCard/> : <DecisionCard/>`
- [ ] **Step 5** bump ?v → 浏览器验真:跑短 run(5 根)→ RunPicker 新条目→选中→流水只显该 run、K 线金框跟随、点行出 RunDecCard 思维链;取消选中回现状

## Task 5: 左栏「当前策略」单卡 + 合议措辞收敛

**Files:**
- Modify: `ui/seats/luozi-app.jsx`、`ui/seats/luozi-panels.jsx`、`ui/seats/luozi-data.jsx`(只动调用点不删函数)

**保形原则:`active` 保持数组形状(全链 8 处依赖),收敛为单元素 `[curStratId]`。**

- [ ] **Step 1** app:新 state `curStratId`(默认 strategies[0]?.id,切 code 重置);`active = curStratId? [curStratId] : []` 替换 activeSet 交集(:51-56);toggleSeat/activeSet 退役;TweaksPanel「席位组合」(:552,667-674)改策略单选(同 curStratId)
- [ ] **Step 2** SeatRail 重做(panels:567-597):头「当前策略 · 单 agent」;单卡=glyph/名/creed 两行 + clock 摘要(止损/止盈/持有/频率)+ perSeat 指标(收益/Sharpe,直取 `symbol.perSeat[curStratId]`)+ MiniLine(eq);strategies>1 时卡头下拉切换;**OrderWatchPanel 改受控** `seatId={curStratId}`(panels:43 内部 seat 状态退役,防左右分叉);「点击启停」删除
- [ ] **Step 3** 合议措辞收敛:MetricsStrip label(app:494、panels:482 '合议'→策略名)、equityLines name(app:477)、收益曲线图例(:519)、distillToCard 文案(:403-407)、Deliberation 多席措辞、consensus 调用点 app:12/153 → 直取 `symbol.perSeat[curStratId]?.eq`;**lzConsensusEquity 函数不删**(fleet 仍引用,Task 7 处理)
- [ ] **Step 4** bump ?v → 浏览器验真:左栏单卡、策略切换联动条件单/头部指标/净值线、回放/真跑无回归

## Task 6: 实盘台账视图(开账 + 今日置顶 + 往日折叠 + 口径切换)

**Files:**
- Modify: `ui/seats/luozi-panels.jsx`(新 LedgerPanel;MetricsStrip live 区)
- Modify: `ui/seats/luozi-app.jsx`(live 右栏装配、onLiveDecide 落台账、ledger state)

- [ ] **Step 1** app:`ledger` state,live 进入+操作后拉 lzLedgerState;**onLiveDecide(:221-232)增强**:边缘标记照旧,且 ledger.opened 时 `lzLedgerPost({kind:'decision', date:今天, code, name, direction, confidence, decision_id, source:'timer'})`;OrderWatchPanel onTrigger 成交(:532)同步 `lzLedgerPost({kind:'trade', side:'buy', price:fill, qty:整手规则, source:'order'})`(qty=Math.floor(可用现金×0.2/price/100)×100,≥100;不足整手跳过并 console.warn 显形)
- [ ] **Step 2** LedgerPanel(live 右栏第一位):
  - 未开账:表单 初始资金(默认 ¥100,000)+ 起始日(默认今天)+「导入影子持仓」勾选(lzShadowListAll 的 open 仓以 entry 价 qty=100/笔入账)→ POST open(+trades);说明「影子组合升级为仓位台账 · 后端持久」
  - 已开账:头部 现金/持仓市值/净值/收益%(equity null 时诚实「估值缺价」);**今日区置顶展开**(今日 decisions+trades+当前持仓表 code/qty/avg_cost/现价/浮盈);**往日按日折叠** accordion(date+计数,点开明细);「盘中自动研判落账」状态行=loopOn 联动,**诚实标注「页面在线时自动」**(第一期前端驱动,后端 scheduler 挂账二期)
- [ ] **Step 3** MetricsStrip live 区(panels:469-532):影子六卡 → 台账口径(净值/现金/持仓市值/今日决策数/胜率(已平)/覆盖);本票⇄组合 toggle 退役(全局一本账);**lzShadow* 函数保留标 deprecated**(迁移入口还要读)
- [ ] **Step 4** bump ?v → 浏览器验真:开账(导入影子)→ 口径切换 → 手动研判落账 → 折叠展开 → **刷新页面台账仍在**(后端持久铁证)

## Task 7: 舰队单 agent 口径 + 哨兵防刷屏

**Files:**
- Modify: `ui/seats/luozi-fleet.jsx`(:41-99 FleetCard、:101-121 FleetGrid)
- Modify: `ui/console/console-data.jsx`(:149-155 哨兵轮询)

- [ ] **Step 1** FleetCard:lzConsensusEquity → `const sid=(window.lzStrategyForCode(code)[0]||{}).id; S.perSeat[sid]?.eq`;「合议 +x%」(:94)→「{策略名} +x%」;今日信号随单策略过滤;FleetGrid 文案(:114)改单 agent 口径;onPick 跳转不动
- [ ] **Step 2** 全局检索 lzConsensusEquity 调用点归零后:函数体保留+注释「单 agent 化后无活跃调用,暂存防外部引用」
- [ ] **Step 3** 哨兵轮询(console-data.jsx:149-155):URL 加 `&exclude_runs=1`(run 回放不刷哨兵徽章);bump console 壳 ?v
- [ ] **Step 4** 浏览器验真:舰队卡正常;跑完 run 后哨兵面板不新增(对照:手动哨兵研判仍亮)

## Task 8: 收口

- [ ] 全量 pytest(预期 ~212 绿,以实跑为准)
- [ ] 重启 9999 + 探活;两处 ?v 终版
- [ ] 浏览器端到端验收(截图给用户):①复盘:真跑短 run→选中→流水+金框跟随→RunDecCard;②换策略再跑→两 run 对比切换;③实盘:开账→落账→今日置顶+往日折叠→刷新仍在;④舰队单 agent 标签;⑤校场演武回归正常(scanSeat 未动铁证)
- [ ] `ui/seats/README.md` 更新;memory 收口(run 化+台账+第一期边界「定时研判前端驱动,后端 scheduler 挂账」)

---

## Self-Review(已执行)

- 规格覆盖:用户五点(①run 选择器+流水+K线跟随 ②撤多席单 agent ③仓位台账+折叠+9:30 记录 ④舰队保留单口径 ⑤演武不入)→ T4/T5/T6/T7/天然满足。
- 占位符扫描:无 TBD;qty 规则/空态文案/降级行为写明。
- 类型一致:run 头字段 T1(c)=T3 Step2;ledger 三 kind=T6 调用;active 数组保形;_isRun 标志 T4 Step3/4 对齐。
- 已知坑回灌:date 必传、数字核匹配、?v bump、9999 重启、append-only、哨兵防刷屏、校准隔离、shape 不兼容、fdecs 变频后不可过滤(daily idx 层做)、影子双写竞态(台账为准)。
