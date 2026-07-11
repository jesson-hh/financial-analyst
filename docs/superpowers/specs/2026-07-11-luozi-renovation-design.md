# 落子页改造 · 设计 spec(2026-07-11)

> 起因:用户「界面逻辑混乱、复盘回测不知道从哪里开始、实盘盯盘逻辑混乱、我也不怎么用,想改造让我真的用起来,好多功能没用可以删减」。
> 审查结论与四项用户决策(2026-07-11 已确认):**方向 A 按任务流重排** / **台账只留决策留痕** / **盯盘升级为后端定时** / **scanSeat 启发式全家删除**。
> 审查详情见 memory `luozi-page-audit-2026-07-11`。

## 0. 审查要点(设计输入)

- 后端 20 端点全真接、前端 26 处调用无断链;乱在壳:三轴八面(盯盘·校场 × 复盘·实盘 × 单标·舰队)、真研判 6 入口 2 管道、「盯盘」一词三义、记账机器空转。
- 使用痕迹:decide 603 条 96% 集中 06-09~15;台账 06-15 后 0 交易;TCA 从未算出真成本;3/4 策略烂尾(绑票不在池);07-11 用户回归用的是**条件单**。
- 「复盘不知从哪开始」直接原因:真跑两个静默失败门(TF 非日/30分 no-op;起点=游标,默认末根→只跑 1 根)。

## 1. 信息架构:一维三页

顶栏页签:**今日 | 复盘 | 策略**。`mode/view/workspace` 三状态轴退役。

### 页 1「今日」(默认,实盘工作台)
- **顶部**:市况条(MarketBar 保留)+ **盯盘总闸**(显示后端 watcher 状态:`● 后端盯盘 · N支 · 今日已判 M/预算`,可开关)。
- **左栏 · 票池列表**(替代舰队网格 + 顶栏股票下拉):固定 6 只 + 动态池(localStorage 保留);选股页「据此落子」交棒照旧入池。每行 = 名/码/现价/涨跌%(轮询)+ 最新真研判徽章 + **盯盘开关**。
  - 盯盘开关语义:开 = 把该票加入其当前策略的 `bind`(无本票策略则绑「动量·默认」);关 = 从**所有**策略 `bind` 移除该票。绑定仍是盯盘集唯一真相(与 ww_seats_bind 同源)。
- **中栏**:StockHero + K 线(TF 全档、新闻泳道、真研判金框、今日 forming 柱全保留)。
- **右栏 4 块**(从 8 块删并):
  1. **研判卡**(LiveDecideFlow + DecisionCard 研判区合一):「▶ 研判一次」(快/深)+ 研判时间线(读 `/seats/decisions?code=`,含 watcher/手动/条件单,修「流水只留 1 条」);每条展开证据链(vintage IC / 叙事 / 价格行为 / 思维链)。
  2. **条件单卡**(OrderWatchPanel 瘦身):⚡立单(日线/5min)、盘中 8s 比对、触发提醒 + K 线金环、复盘验触发保留;删研判循环开关(后端接管)、删成交自动落账、删影子持仓行。
  3. **决策留痕**:按日分组只读时间线(研判/条件单/触发),数据 = `/seats/decisions`。
  4. **五档盘口 · 逐笔**(保留,8s 轮询不变)。
- **删**:LedgerPanel 记账 UI(开账/调仓/持仓/净值/TCA)、影子组合全家、实盘播放条、启发式信号层。

### 页 2「复盘」(回测实验室)
- **顶部 · 新建回测向导**(常驻一行):`票(默认当前) · 策略 · 区间(默认近120交易日) · 粒度(日/30分) → [开始真跑]` → 进度(n/total,可停)→ 完成自动入列并选中。**起止由日期区间计算,与游标/播放彻底解耦**(拔掉两个静默失败门)。
- run 列表(RunPicker,清空水位保留)→ 选中 run:K 线金框 + 净值(纯LLM/混合双线)+ 回测六卡 + 决策流水 + 证据详情卡。
- 新闻泳道 PIT 保留(asof = 选中决策日,回退 run 区间终点);「↺ 提炼为经验卡」保留。
- **删**:逐 bar 推演动画、Deliberation 浮层、播放条、揭示墙随播放的逻辑(标记选中 run 即全显)。

### 页 3「策略」(校场瘦身)
- 保留:策略列表 + 编辑器(名/模板/信条/时钟/w 滑块/PA 开关+方法论/绑票)+ 料库(拖拽配方/回收站)。
- **删**:演武(runArena/strategyArena)、Sharpe 排行与「荐」徽章(数据源=启发式,随删)。验证策略 → 复盘页真跑。
- 一次性清理:删除烂尾策略实体「0612演习策略」「铜陵有色·盯盘」(var/archive + GL 前端迁移);保留「动量 · 默认」「宁德·短线反转」。

## 2. 后端定时盯盘(唯一后端新特性)

- 新模块 `guanlan_v2/seats/watcher.py`:
  - 交易日盘中(09:30–11:30, 13:00–15:00,引擎交易日历判日)每 tick(默认 5min)遍历盯盘集(策略 bind 派生,读 var/archive 的 strategy 实体);
  - 节流:per-code 10min 硬地板 + 策略 `clock.decisionFreq`(hourly/daily),与前端旧口径一致;
  - 门:quote fresh(盘中有真报价)才判;**进程内直调 decide 内核**(严禁 HTTP 自调,守协程红线);
  - 落盘 `seats_decisions.jsonl`,`source:"watcher"`;`industry` 从票池/引擎元数据取真值(顺手修前端定时研判 industry 恒空 bug 的等价后端版)。
- **烧钱保险**:日预算(默认 24 次/日,可调)存 `var/seats_watch.json` `{enabled, daily_budget, counts:{date:n}}`;超限当日自停;env 总闸 `GUANLAN_SEATS_WATCH=1` 才起任务;server 重启按状态文件自恢复。
- 新端点:`GET /seats/watch/status`(enabled/watching codes/today_count/budget/last_tick)、`POST /seats/watch/toggle`(body {on})。
- 帷幄:`ww_seats_bind` 文案「页面开着时前端循环研判」→「后端盘中自动盯(需 GUANLAN_SEATS_WATCH)」;结果 `ww_seats_history` 天然可见。

## 3. 删减清单(代码)

前端:scanSeat 启发式消费端全删(演武、逐bar推演、Deliberation、舰队示意信号/净值、K线启发式标记死层与持仓底色死层);影子组合(data shadow* + localStorage 读写 + 巡检 effect + 面板行);台账记账 UI + TCA 卡;三决策卡合一;fleetWatch 前端循环(60s decide + 7s 盯盘报价轮询)删除,由 watcher 接管;死代码清理(审查清单 13 处:lzConsensusEquity、mulberry32 benchmark、lzRegimeAt/lzRsiOf/lzBuildTriggerCtx/lzSeedDefaultStrategy 死导出、evidenceFor 合成器、SEATS_ALL 冗余席、MetricsStrip 三死 props、FleetCard active 死参、css() 等)。`scanSeat` 本体如无消费者一并删除。

后端:**20 端点全部保留**(calibration/basket_perf/ledger 帷幄在用;/seats/ledger* 前端不再有写入点但端点兼容)。fm_backfill 停 06-06 不在本期(诚实文案保留)。

## 4. 红线(不变量)

- 「真·LLM」徽章只挂真 realDecs/decide 落盘;无真值显「—」/诚实空态,绝不冒充(全仓既有红线)。
- PIT:复盘一切证据 as-of 决策日;watcher 只在盘中判、落盘带 ts。
- 协程内严禁同步自 HTTP(watcher 直调内核)。
- 视觉语言沿用现有中式 tokens(宣纸/月夜主题、印章/朱砂黛绿),**不引入新设计体系**;改 jsx 必 bump html `?v=`。
- 改后端须重启 9999 才生效;9999 由 check_9999.ps1 守护。

## 5. 验收标准

1. pytest 全绿(现有 1127+ 及新增 watcher 测试)。
2. 真机三页 0 console error;三页各自截图/文本核:今日(票池+盯盘开关+研判时间线+条件单+盘口)、复盘(向导一键真跑一个短区间 run 并自动选中出净值)、策略(编辑保存+料库+无演武残留)。
3. watcher 单测:节流/预算/交易日历门/落盘 source;真机 status 端点可读、toggle 可写(盘外不触发 LLM)。
4. ww_seats_bind 绑定 → watch/status 的 watching 立即含该票;ww_seats_history 可见 watcher 决策(盘中窗口验证或单测桩)。
5. 烂尾策略清理后:校场列表 2 个策略、盯盘集不含池外票。
6. 选股页「据此落子」交棒回归:入池 + 聚焦 + 通知条。
