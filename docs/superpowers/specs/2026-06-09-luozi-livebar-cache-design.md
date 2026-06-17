# 落子 · 实盘今日柱缓存(实时源)设计

**日期:** 2026-06-09
**性质:** 单功能设计(纯前端)。修「白天实盘显今日(6-9),收盘后实时源退回 → 图退回 6-8」的复发症,改成把白天记下的今日真实柱缓存住,盘后/隔夜保持图有今天,官方日K入库即让位。
**全局红线:** 只出信号不下单;不写 `G:/stocks`;诚实标注(实时源·待官方结算);改前端 bump `?v` + Chrome MCP @9999 实测;**本功能纯前端,不改后端、不重启 9999**。

---

## 问题根因

实盘「今日柱」现在是**实时**从 `/seats/quote` 报价拼的(`luozi-app.jsx` `dispFrame`,条件 `quote.asofDate > 末根真日K日`)。

- 白天:实时源带今日(asofDate=06-09)→ 拼出今日柱 ✓
- 收盘后/隔夜:实时源退回上一结算日(asofDate=06-08)或拿不到今日 → 条件不成立 → **今日柱当场消失,图退回 6-8**。

没有任何东西把白天看到的今日柱**记下来**。这是要补的那块。

## 用户定义(关键)

缓存柱**不是次一等的展示占位**,而是**今天真实的那根日K**,只是数据源是实时报价而非官方日K(开/高/低/收当日已定盘,完整)。

- **盘中**:agent 要看实时柱生成决策 —— **已成立**,`/seats/order` 后端每次现拉实时报价喂 prompt(`api.py:762/796`),不依赖前端图。本功能不动 agent 通路。
- **盘后**:agent 不再实时交易、不需要喂;但**图必须有今天**。G:/stocks 没更新就用缓存柱占位;官方日K入库即替换。

## 状态机(三态互斥)

锚 = `末根真日K日`(= `symbol.bars[n-1].date`,经 `normDailyBars` 已丢弃 null 占位行,故只在**真实 OHLC** 落库才前进)。

| 条件 | 状态 | 显示 |
|---|---|---|
| 实时报价在且 `quote.asofDate > 末根真日K日` | 盘中 | 实时 forming 柱(最新,优先)+ **同时刷新缓存** |
| 报价撤了/未晚于,但 `缓存.date > 末根真日K日` | 盘后/隔夜 | **缓存柱**(今日真实柱·实时源·待官方结算) |
| `末根真日K日 ≥ 缓存.date` | 官方到了 | **换**:官方 bar 覆盖 + 删缓存 |

## 换的标准(判据)

> **官方那根的真实 OHLC 真正落库、被前端 `normDailyBars`(已丢 null)拿到,末根真日K日 ≥ 缓存柱日期 → 换。**

**不信 `quote.lastBarDate` / 后端占位行**:`api.py:595` `last_bar_date = str(td)[:10]` 只看 trade_date、不管 close 是否 null → 今日 null 占位行一出现就提前变 06-09(这正是 `fresh` 误判、图退 6-8 的根因)。判据只建在前端已丢 null 的真 bar 上,占位行骗不了它。

**检测时机:主动轮询自动换** —— live 模式下每 10min 重拉 `/seats/daily`、null-drop 后比对;命中即用官方 bar 覆盖 `realSyms[code]` + 清缓存。页面开着过夜也无人值守自动接管。

**换的语义:** 硬替换、不合并,官方 bar 全量接管,缓存删除;若官方收盘价与缓存快照有极小差异(实时末刻 tick vs 官方结算价)→ 以官方为准(可能一两分钱跳变,诚实)。

**保险:** 停牌/退市官方永不来 → 缓存柱距今超 ~14 自然日仍未替换 → 停止渲染,防永久幻影。

## 合议 / scan 默认

合议净值曲线 / scanSeat 信号标记**仍只算到已结算 `symbol.bars`**,缓存柱只进 `dispFrame.fbars`(显示帧),不入 `symbol.bars`。

- 原因:盘中那根是半根(收=现价、高低在动),并进确定性净值会乱跳——唯一要守的线。
- 官方日K入库后今日并入 `symbol.bars`,净值/信号自然延伸到今天。
- (盘后那根其实已完整,理论上可让净值/信号延伸;本期默认不延伸,后续可加「盘后完整柱才延伸」判定。)

## 实现单元(纯前端)

### U1 — `luozi-data.jsx` 缓存内核 + 导出
- `LZ_LIVEBAR_KEY(code) = 'guanlan:lz:livebar:' + code`
- `livebarLoad(code)` / `livebarSave(code, bar)` / `livebarClear(code)`(localStorage,坏数据/无效价静默丢弃)
- `livebarFromQuote(quote, lastBarDate)`:`asofDate` 必须晚于 `lastBarDate` 且现价有效才提柱,h/l 夹住 o/c;否则 null
- 导出 `lzLivebarLoad/Save/Clear/FromQuote`
- 缓存 value 形状(localStorage,合成示例值):`{ date:"2026-06-09", o:399.99, h:401.87, l:388.00, c:399.50, v:277351, fresh:false, capturedAt:"2026-06-09 16:14" }`

### U2 — `luozi-app.jsx` 捕获
- `const [liveBar, setLiveBar] = useState(null)`;`useEffect([code])` 载入缓存
- `useEffect([quote, mode, code, n])`:live 下据 `quote` + 末根真日K日提柱,写 state + localStorage

### U3 — `luozi-app.jsx` dispFrame 渲染(改写)
- live + tf==='D':① 实时报价晚于末根 → forming 实时柱(并刷新缓存,U2 已管);② 否则缓存柱晚于末根且未超 14 天 → 缓存柱(`cached:true, forming:false`);③ 否则原 frame

### U4 — `luozi-app.jsx` 官方接管轮询
- `useEffect([mode, code])`:live 下每 10min(+ mount 即查一次)`livebarLoad` 有缓存才拉 `/seats/daily`;`末根真日K日 ≥ 缓存.date` → 覆盖 `realSyms[code]` + `livebarClear` + `setLiveBar(null)`。**deps 不含 liveBar**(否则 6s 捕获不断重置定时器永不触发),缓存从 localStorage 现读

### U5 — `luozi-chart.jsx` 缓存柱视觉 + tooltip
- `cached` 柱渲染为**实心真柱**(符合「相当于真实柱子」)+ 金色「今」标(区别已结算)
- tooltip 加状态行:`forming` → 「实时·盘中未收」;`cached` → 「实时源·待官方结算」

### U6 — bump `?v` 20260609f→g + Chrome MCP 实测

## 验证(Chrome MCP @9999)
1. localStorage 注入一根 `guanlan:lz:livebar:<code>`(date 晚于末根)→ reload → 实盘图右端有该缓存柱、tooltip 标「实时源·待官方结算」、合议净值不含它
2. 实时报价在(盘中模拟)→ forming 柱优先、缓存同步刷新
3. 模拟官方入库(realSyms 末根日 == 缓存.date)→ 缓存柱消失、官方柱接管、localStorage 缓存被清
4. 14 天保险:注入一根 date 距今 >14 天 → 不渲染
