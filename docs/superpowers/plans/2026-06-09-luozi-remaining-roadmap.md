# 落子 · 剩余功能路线图(backlog)

**日期:** 2026-06-09
**性质:** 优先级排序的待办清单,**非**单功能逐步实施计划。每项独立可发;选中某项后再单独走 brainstorm→spec→plan→落地。
**已完成基线:** 第1/2期(研判循环·B1影子)+ 单席位化 + 第3期(校场策略实例)+ 接通配方 + agent研判历史(`?v=20260609c`)。
**全局红线(每项都守):** 只出信号不下单;不写 `G:/stocks`;诚实标注(合成/真实、未回测);改前端 bump `?v`+Chrome MCP 实测;改后端重启 9999。

工作量记号:**S**=半天内 · **M**=1~2 天 · **L**=多日/需新设计。

---

## P1 — 小而高价值(补齐刚建的东西,建议先做)

### 1. 研判历史 删除 / 导出  〔S〕
**目标:** 当前「研判历史」抽屉只读;加单条删除 + 全量导出(JSON/CSV)。
**改动点:**
- 后端 `guanlan_v2/seats/api.py`:`DELETE /seats/decisions/{id}`(读 `var/seats_decisions.jsonl`→剔除该 id→原子重写,复用日历护栏同款 tmp+os.replace 思路防半写)、`GET /seats/decisions/export?format=json|csv`。
- 前端 `luozi-panels.jsx` `DecisionHistory`:每行加「✕ 删」、抽屉头加「导出」。
**依赖:** 无。
**风险:** JSONL 并发写(删 vs decide 落盘)——用原子重写 + 失败不崩。
**验证:** smoke 删一条后 `GET` 少一条;浏览器删/导出。

### 2. decisionFreq 真驱动定时节拍  〔S〕
**目标:** 定时研判现在 `panels.jsx` **每小时封顶写死**(`Date.now()-lastJudgeRef>=3600000`);改成按该策略 `clock.decisionFreq` 真驱动。
**改动点:**
- 前端 `luozi-panels.jsx` `OrderWatchPanel` 定时 effect(当前约 145-151 行):读当前策略 `lzStrategyGet(seat).clock.decisionFreq`,映射节流间隔(`hourly`→1h、`daily`→当日仅一次、更高频→设地板防刷爆 LLM,如 ≥10min)。
**依赖:** 无(decisionFreq 字段已在策略 clock 里)。
**风险:** 防 LLM 被刷爆——设最小间隔地板;仅盘中(`fresh`)才触发(已有)。
**验证:** 浏览器把策略设 daily/hourly,看定时节拍真变;终端确认调用频率。

### 3. 自定义 creed + 落盘存 creed  〔S–M〕
**目标:** creed 现在从模板派生(GL `strategy` 无 `creed` 字段);允许每个策略写**自定义信条**并持久化,且把 creed 落进研判记录(历史可见 agent 当时用的是哪条信条)。
**改动点:**
- 前端 `luozi-data.jsx` `strategySave`:`strategy` 加 `creed` 字段;`lzSeatMeta` 优先用 `strategy.creed`、回退模板 creed。
- `luozi-foundry.jsx` 新建/编辑表单:加 creed 输入(下划线风格,留空=用模板)。
- `luozi-panels.jsx` runDecide/runJudge:已传 creed,自动跟随。
- 后端 decide/order 落盘记录加 `creed` 字段;`DecisionHistory` 展开显示。
**依赖:** 无(GL+localStorage,无需新后端存储)。
**风险:** 老策略迁移(无 creed→显示模板默认)。
**验证:** 新建带自定义信条的策略→研判→历史里看到该信条;reload 持久。

---

## P2 — 中等(让策略闭环更真)

### 4. 策略级独立影子卡  〔M〕
**目标:** B1 影子现按 `code` 记账(`guanlan:lz:shadow:<code>`)+ 本票/组合聚合;改成**每个策略实例一本独立影子账**,绩效归因到策略而非票。
**改动点:**
- 前端 `luozi-data.jsx` shadow 内核:键改 `guanlan:lz:shadow:<strategyId>`(或 `<strategyId>@<code>`);`shadowAggregate` 按策略汇;`onTrigger` 已带 `seat=strategy.id`。
- `luozi-app.jsx`/`MetricsStrip`:加「本策略/全部」视图。
**依赖:** 无,但**需迁移**已有按 code 的影子账(写一次性迁移:旧 code 账→按其触发的 strategy 拆,或保留 code 账并旁挂策略账)。
**风险:** 数据迁移;一票多策略时成交归属。
**验证:** 合成两策略注入→各自影子账精确还原;reload 持久。

### 5. 校场演武用真日 K(替合成)  〔M〕
**目标:** `strategyArena` 现用合成 `LZ_SYMBOLS` bars,与桌面真日 K 数字不一致(已知诚实边界);改成拉真日 K 演武。
**改动点:**
- 前端 `luozi-foundry.jsx` `strategyArena`:对每个绑定 code 走 `/seats/daily`(真日 K,已有)取 bars,再跑 `scanSeat`+`seatEquity`;无数据的票诚实跳过+标注。
**依赖:** 后端 `/seats/daily`(已就绪);依赖日历健康(护栏已加)。
**风险:** 绑定票可能无数据/窗口不齐;异步加载态;演武变慢(可缓存)。
**验证:** 校场演武数字 == 桌面同策略回测;无数据票诚实标。

### 6. 5min execTF 演武  〔M,依赖 #5 的真 bars 基建〕
**目标:** 策略 `clock.execTF==='5min'` 时,演武走 5 分钟 K(现只走日线)。
**改动点:**
- 前端 `luozi-foundry.jsx` `strategyArena`:execTF 5min 时用 `lzFetchBars5`/`/seats/daily?freq=5min` 取 5min bars 跑 scanSeat;clock 的止损/止盈/最长持有按 5min 根数解释。
**依赖:** #5(真 bars 加载路径);后端 5min 数据(`/seats/daily?freq=5min`、`/seats/bars_live` 已有)。
**风险:** 5min 窗口大小/持有根数口径;数据量。
**验证:** 5min 策略演武出独立结果,与日线策略区分。

---

## P3 — 大 / 可选

### 7. 合议 = agent 真商量(替确定性等权)  〔L,可选〕
**目标:** 复盘/实盘「合议」现为**确定性等权平均**各席净值(诚实标注过,非 LLM)。若你要"合议也是 agent 商量出来的",需做多席 LLM 研判→汇总/辩论→共识。
**改动点:** 后端新增"合议"端点(并行调各策略 decide→汇总成共识,或一轮简短辩论);前端合议卡接真调用 + 落盘 + 历史。
**依赖:** decide 已就绪;需新设计共识规则。
**风险:** 慢(N 席 ×LLM)、贵、需缓存;**易被误当"每次都一样"——要诚实展示这是真调**。
**判断:** 仅当你确实想要 LLM 合议再做;否则保持现状(确定性,已诚实标)。

---

## 杂项 / 交付

### 8. canonical 日历护栏同步〔交付,非开发〕
`G:/stocks/src/data/bin_writer.py`(红线,我不碰)仍无护栏——我把 `financial-analyst/src` 那份的准确 diff 给你,你自己贴过去,三份一致。**否则从 G:/stocks 重新 vendor 会覆盖护栏。**

### 9. B2 回填真实成交 —— 明确不做
系统只出信号、不接真账户(设计红线)。除非你要接券商/真实成交,否则**永久搁置**。

---

## 建议执行顺序
**P1 全做(1→2→3,都 S 级,一两轮搞定,把刚建的研判/循环补圆)** → 再看 P2 要不要(4/5/6,5 是消除"合成vs真"不一致的关键,6 依赖 5)→ P3-#7 只在你明确要 LLM 合议时做 → #8 我随时给 diff。

每项选中后单独 brainstorm→spec→plan,不混做。
