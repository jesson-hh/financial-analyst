# 落子 K 线新闻标记泳道设计(回测 PIT + 实时)

- 日期:2026-07-03
- 缘起:用户读 fmz.com/digest-topic/10970(FUSE:把新闻画在 K 线上),要求"进回测落子平台把新闻标注进来,实时的时候也可以标注"。
- 红线(贯穿全项目):**准确、不幻觉、信息量大**;**回测严格 PIT,不偷看决策日之后的新闻**。

## 1. 目标与非目标

**目标**:在落子校场的自绘 SVG K 线图上,叠加一条"新闻标记泳道",与既有研判落子标记并存不冲突。两态:
- **回测·回放**:标记来自历史 PIT 语料,只显示 as-of 之前可见的新闻。
- **实时·盯盘**:同一条泳道,标记来自直播快讯流,右端随行情生长。

**非目标(本期不做,列入 backlog)**:把 news_marks 烤进 run 产物做冻结复现(方案③);金十(jin10)连接器;console `ww_news_pit` 工具对等;akshare parquet 读取器;新闻情绪给徽章上色。

## 2. 已定设计决策(用户逐点确认)

1. **两态都上**:回测回放 + 实时盯盘,复用同一 `CandleChart` 组件(`live`/`asOf` props)。
2. **标记范围 = 全量快讯 + 关键词过滤**(最贴 FUSE):画当日全部快讯(含宏观/政策,如加息/非农),用户用关键词收窄。数据来源为 `pit_store` 的 `news.jsonl` + `policy.jsonl` + 逐票 `events.jsonl`。
3. **渲染模型 = 聚类徽章 + 侧栏下钻**:每 bar 一个 `▣ N` 计数徽章(N=当 bar 命中条数);hover 显前 3 标题;点击展开当 bar 全部条目到右栏"当日快讯"面板。
4. **关键词过滤放前端**:对后端返回的 PIT 全集即时正则过滤(`铜陵有色|000630|加息` 式),不重取;命中的 bar 徽章高亮(金),默认预填**本票名称 + code**,清空看全量。
5. **PIT 双闸**:后端 `ts ≤ as-of` 过滤 + 前端揭示墙(`revealTo`)双保险。
6. **诚实降级**:`asof < 2026-05-20`(coverage_floor)或超出 pit_store 覆盖范围 → 徽章打灰 + "覆盖不全/无"标注;空即诚实空,绝不编造。
7. **不接金十**:东财/新浪 7×24 快讯为等价物,已生产就绪。

## 3. 方案选型

**方案①(采纳):专用 PIT 新闻服务 + 单一端点 `GET /seats/news`。** 一处装配整段可视窗口的扁平 PIT 新闻流,回测走 `PitReader`(pit_store),实时走 `KuaixunNewsProvider`;前端按当前 tf 聚类。服务整窗全 bar、回测/实时/纯浏览通用、后端+前端双闸。

**方案②(否决):搭 `/seats/decide` 顺风车。** decide 只在决策锚点算,给不了整条 120+ bar 的新闻时间线;铺满全图需对每 bar 循环 decide,浪费。仅适合"决策那一刻的新闻",不是泳道。

**方案③(留 backlog):建 run 时把 news_marks 烤进 run 产物。** 可回测复现、秒载,但只服务"有 run"场景,纯浏览不行,实时仍需另路。作为①之上的后续增强。

## 4. 架构与模块

| 层 | 文件 | 职责 | 改/新 |
|---|---|---|---|
| PIT 读取 | `engine/financial_analyst/backtest/pit_reader.py` | `get_visible_info(date, as_of, boundary_ts, code)` 按 `ts ≤ boundary` 过滤 | **不改·复用** |
| 装配器 | `guanlan_v2/seats/news_marks.py` | `assemble_news_marks(code, asof, mode, window)` → 回测扫 pit_store 窗口 / 实时调 KuaixunNewsProvider;归一化、排序、覆盖注记 | **新** |
| 路由 | `guanlan_v2/seats/api.py` | `GET /seats/news` 薄壳,`asyncio.to_thread` 包同步读 | **改** |
| 实时 | `engine/financial_analyst/watch/*`,`buddy/server.py` `/watch/stream` SSE | 每 tick 附 `news_marks`(live 全量快讯) | **改** |
| 前端数据 | `ui/seats/luozi-data.jsx` | `fetchNews(code, asof, mode)` + `mapNewsToFrame(items, fbars)` 按 bar 聚类 | **改** |
| 前端图 | `ui/seats/luozi-chart.jsx` | 价格上方新 `<g>` 新闻泳道:`▣ N` 徽章 + hover 前3 + 命中高亮;受 `revealTo` 约束 | **改** |
| 前端壳 | `ui/seats/luozi-app.jsx` | 关键词过滤框 + 右栏"当日快讯"下钻面板 + 接线 `newsMarkers` | **改** |

**职责边界**:tf→bar 聚类只在前端(帧是前端所有);关键词过滤纯前端(即时);后端只判"哪些在 as-of 前可见"并吐扁平流。

## 5. 数据契约

### 5.1 pit_store 源(只读,已存在)
- 根:`G:\stocks\stock_data\pit_store\{YYYY-MM-DD}\{news,events,policy}.jsonl`,`_meta.json`
- 范围:2026-03-13 → 2026-07-01(74 交易日);`news_coverage_floor: 2026-05-20`
- `news.jsonl`:`ts`(ISO 分钟)、`date`、`session`、`code`(宏观为 null)、`title`、`body`、`source`、`provider`
- `events.jsonl`:`ann_date`、`code`、`type`、`summary`、`fields.visible_ts`(**可见时间戳,PIT 用它而非 trade_date**)、`source`
- `policy.jsonl`:`pub_date`、`ts`、`level`(gov|macro_news)、`title`、`summary`、`tags`、`code`

### 5.2 后端 `GET /seats/news`
请求:`?code=SZ000630&asof=2026-06-01&mode=pit&window=250`
- `mode=pit`:`asof` 必填 → PIT 过滤 `ts ≤ asof 边界`(边界与 `/seats/decide` 同源:api.py 现有 asof 计算)。`window`=向前回溯的交易日数(覆盖可视窗口)。
- `mode=live`:返回今日直播快讯(KuaixunNewsProvider),忽略 asof。

响应:
```json
{
  "ok": true,
  "code": "SZ000630",
  "mode": "pit",
  "asof": "2026-06-01",
  "items": [
    {"ts":"2026-06-01T09:31:00","date":"2026-06-01","title":"央行上调 MLF 利率 10 个基点","source":"sina_7x24","level":"macro","code":null,"body_head":"…"}
  ],
  "coverage": {"floor":"2026-05-20","range":["2026-03-13","2026-07-01"],"partial":false,"note":""},
  "provenance": {"source":"pit_store","rows":128}
}
```

归一化后的 item(news/policy/events 三路统一):
```
{ ts, date, title, source, level, code, body_head }
level ∈ {"stock"(命中 code), "macro", "policy", "event"}
```
- `items` 按 `ts` 升序;后端只做"可见性"裁剪,不做关键词过滤、不做聚类。

### 5.3 前端 `mapNewsToFrame(items, fbars)` 产出
```
newsMarkers: [ { idx, count, hit, items:[{ts,title,source,level,code,body_head}] } ]
```
- 聚类:同一 tf 帧 bar 内的 items 归一桶(日线按 `date`;分钟按 minute-bar 边界),**复用 `mapDecsToFrame` 的 locate 逻辑**。
- `hit`:桶内任一 item 的 title 命中当前关键词(`|` 分割正则);关键词非空时命中桶徽章高亮(金),非命中桶保留中性计数。
- 揭示:`idx > revealTo` 的桶**不渲染**(前端 PIT 闸)。

### 5.4 实时 SSE
`/watch/stream` 的 `quote_update` 事件附 `news_marks:[{ts,title,source,level,code}]`(今日,追加式);前端 live 分支并入泳道的 forming/最右 bar。

## 6. 回测数据流 + PIT 保证

```
选票+run → 前端取 reveal 游标→ asof → GET /seats/news?code&asof&mode=pit
 → 后端 assemble_news_marks:遍历 pit_store[开窗..asof] 各日,
    news/policy 滤 ts≤asof边界,events 滤 visible_ts≤asof边界
 → 扁平流(升序)回前端 → mapNewsToFrame 按当前 tf 聚类
 → 泳道渲染,且 idx>revealTo 不画(揭示墙)
```
**保证**:①后端可见性裁剪与 `/seats/decide` 用同一 asof/边界,决策与新闻口径一致;②events 用 `visible_ts` 非 `trade_date`,不漏;③前端揭示墙二次拦截。三者叠加 → 回测绝不显示 as-of 之后新闻。逐条可追溯到 pit_store 源行,不编造。

## 7. 实时数据流

```
/watch/stream 每 tick → KuaixunNewsProvider(code) 取当日全量快讯
 → 附 news_marks 进 SSE → 前端 live 分支并入同一泳道(ts=当刻)
```
实时无 PIT 约束(本就是"现在");无揭示墙,右端随行情生长。图形与回测一致,仅数据来源与生长方向不同。

## 8. 前端渲染(泳道 + 关键词 + 下钻)

- **泳道**:价格区上方独立 lane;每 bar `▣ N` 徽章(中性色 `--ink-3`,避开红绿买卖专用色);命中关键词 bar 徽章金色高亮 + 选中环;细旗杆虚线连到该 bar 高点。
- **关键词框**:校场工具条输入;默认预填本票名 + code;`|` 分割;纯前端即时过滤(不重取)。清空 → 显全量(此时依赖聚类避免刷屏)。
- **下钻**:点徽章 → 右栏"当日快讯"面板列该 bar 全部条目(时间 · 来源徽章 · 标题 · 正文首段),命中条高亮置顶,可展开正文;hover 徽章 → 浮层显前 3 标题。
- **叠加不动既有**:B/S 研判落子标记、真·思考金框、条件单触发环全部原样;新闻泳道只新增一个 `<g>`。

## 9. 诚实降级(红线)

| 情形 | 后端 | 前端 |
|---|---|---|
| `asof < 2026-05-20`(coverage_floor) | `coverage.partial=true` + note | 该区徽章打灰 + 角标"覆盖不全 <05-20" |
| `asof` 超出 pit_store 范围 | `items=[]` + note "无 pit_store 覆盖" | 泳道空 + 提示条 |
| 2026-07-01 周末滚存语义降级 | note 提示 | 徽章加"?"提示 |
| 读取失败 | `ok:false` 或空 items + note | 泳道静默空,绝不伪造 |

## 10. 测试

后端(`tests/`,`PYTHONPATH=engine`):
1. `test_news_marks_pit_no_future`:某 asof 装配,断言无 item `ts > asof 边界`。
2. `test_news_marks_events_use_visible_ts`:构造 `trade_date ≤ asof` 但 `visible_ts > asof` 的 event → 被裁。
3. `test_news_marks_coverage_floor`:`asof < floor` → `partial=true` + note,稀疏/空。
4. `test_news_marks_out_of_range`:asof 越界 → 空 + note,不崩。
5. `test_news_marks_item_shape`:三路归一化后键齐全、level 正确、code=null 宏观正常。
6. 端到端:真 pit_store 抽样(如 SZ000630,asof 取 05-20 后覆盖良好日)→ 有徽章、无越墙。

前端:`mapNewsToFrame` 聚类(日线聚一天、30 分聚半时)、关键词命中/清空、`idx>reveal` 不渲染。若有前端测试跑器则单测,否则以 preview 手验为准并记录。

## 11. 验收口径

- 真 pit_store 上某覆盖良好日跑回测态:泳道有徽章、点开下钻条目逐条可回查源行、揭示墙右无新闻。
- 关键词过滤即时生效、命中高亮。
- coverage_floor 前打灰、越界诚实空。
- 全量 pytest 绿;既有落子标记与研判不受影响。
- 实时态:同图长出直播徽章(需 watch feed 环境)。
