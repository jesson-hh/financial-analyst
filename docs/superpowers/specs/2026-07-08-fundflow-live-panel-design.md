# 观澜 · 板块资金流向(实时)设计

- 日期:2026-07-08
- 状态:设计定稿(待用户过目)
- 参照:用户所示「板块资金流向」App 截图(盘中累计净流入多线图 + 大盘超大/大/中/小/主力分解 + 全A/行业/概念涨跌家数 + 板块净流入排行榜)
- 架构母版:`guanlan_v2/macro/`(全球情绪温度计,2026-07-06 spec)——现拉 → 聚合 → 快照沉淀 → 前端纯 SVG 展示
- 区分:`2026-06-22-fundflow-factor-family-design.md` 是**个股资金流因子族**(因子库),本文是**板块资金流实时展示页**,两不相干

## 1. 背景与目标

用户希望在观澜新增一个**实时资金流向**独立页,复刻截图形态:

1. **盘中累计净流入多线图**——各板块随时间(09:30–15:00)的今日主力净额曲线,从开盘 0 附近发散(算力概念 +94.50亿 一路向上、存储芯片 −152.82亿 一路向下)。
2. **大盘资金分解**——右上角小单 / 中单 / 超大单 / 大单 / 主力 五档当日净额。
3. **涨跌家数头条**——全A 涨1886 跌3458、行业 涨149 跌347、概念 涨178 跌317。
4. **板块净流入排行榜**——按今日主力净额排序(前部净流入、尾部净流出)。

口径:概念板块 + 行业板块**双档切换**。

**核心定位:纯展示层,绝不混入任何交易信号**(与温度计同红线)。资金流是观察量,不回写选股/落子的任何评分。

## 2. 非目标(YAGNI)

- 不做个股级资金流(已有 `eastmoney_fund_flow` per-stock 源,本页不用)。
- 不做地域板块(只概念 + 行业两档)。
- 不做历史跨日趋势(只当日盘中;跨日靠按日快照文件天然留痕,但页面只展示当日)。
- 不做资金流因子/信号/预测——纯展示。
- 不做告警、推送、盯盘循环。
- 首版不做移动端专门适配(沿用观澜桌面栅格,窄屏能读即可)。

## 3. 总体架构

```
G:\stocks 正典数据                guanlan_v2\fundflow\ 新模块           ui\fundflow\ 新页面
  src/data/live_sources.py         sources.py  现拉三源(走 datafeed      观澜 · 资金流向.html(外壳)
   探针注册表 +3 源:                          .live_client.probe)        fundflow-data.jsx(fetch 层)
   · eastmoney_sector_fund_flow    pulse.py    聚合 + 落当日快照 +         fundflow-app.jsx(React 组件树)
   · eastmoney_market_fund_flow                history 重建盘中多线          · 涨跌头条 strip
   · eastmoney_market_breadth      api.py      /fundflow/live             · 大盘分解柱
   src/data/live_sources_impl/                 /fundflow/history           · SVG 多线盘中图
   （新增 3 个 handler 实现）                                              · 排行榜(盘中 Δ 徽章)
                                   var\fundflow\YYYYMMDD.jsonl           · 概念/行业双档 Tab
                                   （追加式,按交易日轮转）
guanlan_v2\datafeed\live_client.py                                      ui\_shared\guanlan-nav.js
  _STATIC_SOURCES / NEED_CODE 表 +3 别名                                  MODULES +1 页签
guanlan_v2\console\tools.py                                             guanlan_v2\server.py
  ww_fundflow 帷幄工具(+CONSOLE_ALLOWED 白名单四处同步)                    include_router + opt-in poller
```

数据只有一个现拉门户:`guanlan_v2/datafeed/live_client.py` → `G:\stocks` 探针子进程。guanlan 侧不直连东财、不复造反封 HTTP 客户端。

## 4. 数据源(stocks 探针 3 个新源)

### 4.1 为什么走 stocks 探针而非直连 akshare

真机实测(2026-07-08):
- 裸 akshare 调 push2(`stock_sector_fund_flow_rank` / `stock_zh_a_spot_em`)→ 代理错误,`NO_PROXY=*` 后 → `RemoteDisconnected`(东财反爬)。
- stocks 探针的 blessed 客户端跑 `eastmoney_industry_comparison`(同 push2 `clist/get`)与 `eastmoney_fund_flow`(per-stock)→ `status=ok`,真数据返回。

结论:push2 只经 stocks 的带头/带节流/带重试的 blessed session 才稳。板块资金流是同族 `clist/get`(仅 `fs`/`fields` 不同),把握大。

### 4.2 三个新源契约

统一沿用 stocks 探针信封:`{status: ok|planned|error, items:[{raw:{…}}], error, fetched_ts}`。

**源 A — `eastmoney_sector_fund_flow`(别名 `sector_fund_flow`)**
- 入参:`code` 复用为**档位选择**——`concept`(概念)/ `industry`(行业);缺省 `concept`(贴截图题材视角)。经 `CODE_PASSTHROUGH` 透传(非 6 位股票码)。
- push2 `clist/get`,`fs=m:90 t:3`(概念板块)/ `m:90 t:2`(行业板块),`fields` 取资金流字段(`f62` 主力净额、`f184` 主力净占比、`f66/f69` 超大、`f72/f75` 大、`f78/f81` 中、`f84/f87` 小、`f3` 涨跌幅、`f204` 领涨股、`f12` 板块码、`f14` 板块名)。
- 输出 `raw` 契约(每行一个板块):
  ```
  {board_code, board_name, main_net, main_pct, super_net, large_net, mid_net, small_net,
   change_pct, up_count?, down_count?, leader_name?, leader_code?}
  ```
  金额单位统一**元**(前端再折算亿)。`main_net = super_net + large_net`(东财口径,handler 内校验并在偏差时以上游 `f62` 为准)。

**源 B — `eastmoney_market_fund_flow`(别名 `market_fund_flow`)**
- 无参。全市场当日五档净额 `{super_net, large_net, mid_net, small_net, main_net}`(截图右上"大盘资金")。
- 首选接口:push2 大盘资金流实时(secid `1.000001`+`0.399001` 合计,或东财 `f62` 家族的市场级端点);实现时以真机探针钉死确切 endpoint。

**源 C — `eastmoney_market_breadth`(别名 `market_breadth`)**
- 无参。返回三组涨跌家数:`{allA:{up,down,flat}, industry:{up,down}, concept:{up,down}}`(截图头条)。
- 首选:全A 计数经东财市场统计端点;行业/概念涨跌数可由源 A 的 `up_count/down_count` 加总兜出。

### 4.3 不确定点与诚实兜底(实现时钉死,绝不编造)

| 源 | 确定度 | 兜底(降级显形,不假装) |
|----|--------|--------------------------|
| A 板块资金流 | 高(同 `industry_comparison` 族) | 若概念档 `fs` 需微调,以真机 catalog 校准;拉空则 `status` 显形、前端标"本次 0 行" |
| B 大盘分解 | 中(确切 endpoint 待钉) | ① 由源 A 全板块 `super/large/mid/small_net` 加总近似;② 退当日 `stock_market_fund_flow` 最新行 + `stale` 标记 |
| C 涨跌家数 | 中(全A 计数端点待钉) | 行业/概念用源 A `up/down_count` 加总;全A 拉不到则该组显"—"并 note,不用板块数冒充全A |

每个兜底都在 payload `notes[]` 里明写触发原因;温度计同款"降级条"在页脚显形。

### 4.4 datafeed live_client 接线

`guanlan_v2/datafeed/live_client.py`:
- `_STATIC_SOURCES` 加三行:`eastmoney_sector_fund_flow: sector_fund_flow`、`eastmoney_market_fund_flow: market_fund_flow`、`eastmoney_market_breadth: market_breadth`。
- `CODE_PASSTHROUGH` 加 `eastmoney_sector_fund_flow`(code 承载 concept/industry 档位,不可被 6 位提取毁掉)。
- B、C 无参,不入 `NEED_CODE`。
- `test_datafeed_client::test_static_sources_reconcile_with_stocks_registry` 会对账 stocks 注册表——两侧同批加,守护通过。

## 5. 后端模块 `guanlan_v2/fundflow/`

### 5.1 `sources.py` — 现拉三腿

仿 `macro/astock.py::_client_live`:统一经 `datafeed.live_client.probe(source, code, limit)` → `native_rows`。三个薄函数 `fetch_sector(kind)` / `fetch_market()` / `fetch_breadth()`,各自返回 `{ok, rows, note}`,失败诚实置 `ok=False` + `note`,绝不抛穿。

### 5.2 `pulse.py` — 聚合 + 快照沉淀 + history 重建

母版 `macro/pulse.py`。

**`build_live(kind, refresh, poll)` → 当前快照 payload**
```
{ok, kind, pulled_at, trading:bool, stale_minutes|null,
 market:{super_net,large_net,mid_net,small_net,main_net},
 breadth:{allA,industry,concept},
 boards:[{board_code,board_name,main_net,change_pct,rank,delta_intraday}],  # 排行榜(按 main_net 降序)
 notes:[…]}
```
- 现拉三源(sector 按 `kind`、market、breadth)。任一失败 → 该块降级 + note,不拖垮其余。
- 交易时段判定:本地 09:30–11:30 / 13:00–15:00 且工作日 → `trading=true`。
- **落点**:每次真拉到 sector 行(`boards` 非空)就向 `var/fundflow/<today>.jsonl` 追加一行快照(见 5.3)。非交易时段也可拉(得到上一完整档),但**只在 `trading` 或显式 `refresh` 时落点**,避免污染当日线基线。
- `delta_intraday`:该板块 `main_net` 减去当日首个快照的同板块值(≈从开盘至今的增量);无首快照则 `null`。

**`load_history(kind, date)` → 盘中多线序列**
```
{date, kind, ticks:[ts,…],
 boards:[{board_name, series:[main_net@each ts]}],   # 选中的 top-N 板块
 market_series:{main_net:[…@each ts]},
 breadth_series?:…}
```
- 读 `var/fundflow/<date>.jsonl`(缺省今日),逐行还原。
- **选线规则**:取当日最末快照里 `main_net` **净流入前 8 + 净流出前 8**(共 ≤16 条);每条板块回填其在每个 tick 的 `main_net`(某 tick 缺该板块则该点断开,不插值)。
- `market_series.main_net`:大盘主力净额时间序列(单独一条粗线,可选叠加)。

### 5.3 快照数据模型 `var/fundflow/YYYYMMDD.jsonl`

追加式,一行一次拉取,按交易日一个文件(次日换文件 → 曲线次日自动从开盘重起):
```json
{"ts":"2026-07-08T10:57:03","kind":"concept",
 "market":{"super_net":-1.93e10,"large_net":-2.17e10,"mid_net":-1.39e8,"small_net":4.11e10,"main_net":-4.10e10},
 "breadth":{"allA":{"up":1886,"down":3458,"flat":0},"industry":{"up":149,"down":347},"concept":{"up":178,"down":317}},
 "boards":[{"board_code":"BK...","board_name":"算力概念","main_net":9.45e9,"change_pct":2.1}, …]}
```
- 概念、行业**分档各落各的**(`kind` 字段区分);`load_history(kind)` 只取同档行。
- 脏行跳过(jsonl 惯例);读失败返空 + note。
- 轮转:文件名即交易日,天然分文件,无需清理逻辑(旧文件留档,页面不读)。

### 5.4 `api.py` — 薄壳路由(无 prefix,协程内 `asyncio.to_thread`)

```
GET /fundflow/live?kind=concept|industry&refresh=0|1   → build_live(...)
GET /fundflow/history?kind=concept|industry&date=YYYYMMDD → load_history(...)
```
仿 `macro/api.py`,同步实现一律 `await asyncio.to_thread(...)`(严禁协程内同步 HTTP,否则堵 loop 触发 9999 看门狗杀)。

## 6. 实时驱动(诚实节奏)

两条腿,叠加:

1. **On-view 落点**——前端打开/刷新即 `GET /fundflow/live?refresh=1`,后端顺手落一个快照点。有人看 → 线自然生长。
2. **Opt-in 服务端 poller**——`server.py` `start_fundflow_poller()`,默认关,`GUANLAN_FUNDFLOW_POLL=1` 才启(仿 `start_regen_daily_scheduler` / `start_market_status_scheduler`)。进程内 daemon 线程,交易时段每 **3 分钟**拉一次并落点(概念 + 行业各一次);非交易时段休眠。定时器随本进程存亡——**非 24/7 保证**,进程死即停,`notes` 显形当前是否开启。

关着 poller 时前端明写:"线图仅在查看时落点;开 `GUANLAN_FUNDFLOW_POLL=1` 可全时段成线。"红线:绝不宣称 24/7 自动盯盘。

## 7. 前端 `ui/fundflow/`

### 7.1 外壳 `观澜 · 资金流向.html`

复制 `macro/观澜 · 全球情绪.html`:同字体、`../industry/gl-ds.css`、React 18.3.1 UMD + Babel standalone、`guanlan-bus.js?v=4` + `guanlan-nav.js`(bump `?v`)、引 `fundflow-data.jsx` + `fundflow-app.jsx`。`<title>观澜 · 资金流向</title>`。

### 7.2 数据层 `fundflow-data.jsx`

仿 `macro-data.jsx`:
```
glFetchFundflowLive(kind, refresh)      → GET /fundflow/live
glFetchFundflowHistory(kind, date)      → GET /fundflow/history
```
`window.GUANLAN_BACKEND` 为空(file:// 直开)→ 返回 `{ok:false, reason:"经 9999 访问"}`,不合成假数据。

### 7.3 组件树 `fundflow-app.jsx`

```
App
 ├─ 页头:标题「板块资金流向」+ 概念/行业 Tab 切换 + 现拉时间/陈旧徽章 + 刷新钮
 ├─ BreadthStrip     涨跌头条(全A / 行业 / 概念,红涨绿跌)
 ├─ MarketFlowBars   大盘五档(超大/大/中/小/主力)水平条,正红负绿 + 亿元数
 ├─ IntradayChart    SVG 多线盘中图(核心)
 ├─ BoardRankTable   板块排行榜(名次 · 板块名 · 主力净额亿 · 盘中Δ徽章 · 涨跌幅)
 └─ NotesBar         降级条(源失败/快照陈旧/poller 状态)
```

**`IntradayChart`(纯 SVG,无图表库,放大版温度计 `Spark`)**:
- x = 当日 tick 序(09:30 起),y = 主力净额(元;轴用亿);0 线居中,正上负下。
- ≤16 条 `polyline`:净流入板块用朱/暖色系、净流出用青/黛冷色系(A股惯例:红涨绿跌 → 净流入红、净流出绿)。
- 每条右缘贴板块名 + 当前净额亿(截图那样),重叠时按 y 错开。
- 大盘主力净额单独一条加粗基准线(可 toggle)。
- 数据 <2 tick → 占位:"盘中数据累计中(每次刷新落一点,开盘后逐步成线)"。
- A股色约定沿用主题:`--zhu`(朱/红,涨/净流入)、`--dai` 或 `--qing`(绿,跌/净流出)、`--ink-*`、`--jin`。

**红涨绿跌**:全站资金流展示统一 A股口径(与温度计的 risk-on/off 色带无关);正值红、负值绿,前端一处 `flowColor(v)` 收口。

### 7.4 观澜导航接线

`ui/_shared/guanlan-nav.js` `MODULES` 加:
```js
{ label: '资金流向', file: '../fundflow/观澜 · 资金流向.html' }
```
放在 '全球情绪' 之后。bump 所有引用页的 `guanlan-nav.js?v=`(现 v=4 → v=5,全站页一起 bump 免缓存串味)。

## 8. 服务端接线 `guanlan_v2/server.py`

`build_app()` 内(macro router 之后):
```python
from guanlan_v2.fundflow import build_fundflow_router
app.include_router(build_fundflow_router())
from guanlan_v2.fundflow.api import start_fundflow_poller
start_fundflow_poller()   # opt-in;GUANLAN_FUNDFLOW_POLL=1 才真起
```
静态页由既有 `_UI_DIR` 自动服务(macro 页同理),无额外挂载。**改后端须重启 9999 才生效**(memory:backend-lives-in-repo)。

## 9. 帷幄工具 `ww_fundflow`

`guanlan_v2/console/tools.py` 加 `ww_fundflow(kind="concept")`:现拉当前板块资金流(排行榜前 N + 大盘分解 + 涨跌头条)→ 组装**全量 content**(教训:console 工具无 content 键会被 `_wrap` 截 400,agent 只见断裂 JSON;数据型工具必组全量 content + 回归穿 `_wrap` 信封级测试)。同步四处:specs 白名单 + `CONSOLE_ALLOWED` + `_SYSTEM_PROMPT` + 守护计数(memory:weiwo-capability-expansion)。计数相应 +1。

## 10. 诚实与降级红线

- **纯展示,绝不当交易信号**——不回写任何选股/落子评分(与温度计同)。
- 单源失败/快照陈旧/poller 未开 → `notes[]` 显形,页脚降级条呈现。
- 真错(源非法/子进程失败)→ `ok:False`,前端断供占位,不合成假数据。
- 大盘/涨跌兜底触发时明写"由板块加总近似"或"退当日行·陈旧",不冒充实时精确值。
- 金额单位全链一致(元 → 前端折亿),防量纲串味(memory 有量纲踩坑史)。

## 11. 测试计划

**stocks 仓(G:\stocks)**
- 三个新 handler 单测:mock push2 响应 → 断言 `raw` 契约字段齐、金额为元、`main_net=super+large` 校验、概念/行业 `fs` 正确、拉空 `status` 显形。
- 探针注册表对账测更新(source_id 集合 +3)。

**guanlan 仓**
- `test_fundflow_sources`:三腿失败/成功/降级路径(注入假 `probe`)。
- `test_fundflow_pulse`:build_live 落点、非交易不落、delta_intraday、快照脏行跳过;load_history top8+8 选线、缺 tick 断点不插值、按 kind 隔离、按日文件轮转。
- `test_fundflow_api`:两端点 200 + payload 形;协程 to_thread(不堵 loop)。
- `test_datafeed_client`:三别名 resolve + reconcile 守护过。
- `test_console_tools`:ww_fundflow 穿 `_wrap` 信封、全量 content 不被截断。

**真机探活(实现末)**
- 跑 `probe_live_sources.py --source=eastmoney_sector_fund_flow --code=concept` / `industry`、`--source=eastmoney_market_fund_flow`、`--source=eastmoney_market_breadth`,确认三源真返数据、钉死 B/C 确切 endpoint。
- 起 9998(避杀 9999 看门狗)验 `/fundflow/live` + `/fundflow/history`,前端浏览器核对多线图渲染。

**回归**:全量 pytest 绿(现基线 ~1024/0)。

## 12. 默认参数(用户未另指定即用)

- 盘中图线数:今日净流入前 8 + 净流出前 8(≤16 条)。
- 自动落点间隔:3 分钟(交易时段;`GUANLAN_FUNDFLOW_POLL_SEC` 可调)。
- 排行榜条数:前 20。
- 板块档位缺省:`concept`(概念,贴截图),Tab 可切行业。

## 13. 挂账与风险

- **B 大盘分解 / C 涨跌家数确切 push2 endpoint 未钉死**——最高优先在实现首步用真机探针定案,含兜底路径实测。
- 盘中线依赖 poller 或有人查看;首日/进程重启后当日线从重启点起(非全天)——honest note 交代。
- 跨仓改动(stocks + guanlan)需两侧测试各自绿;stocks 缺席的机器上 guanlan 侧源探活会 `ok=False` 降级(可接受)。
- 交易日历用简化的时段判定(未接节假日日历);非交易日拉到上一档,`trading=false` 显形,可接受。

## 14. 交付顺序(供 writing-plans)

1. stocks:三 handler + 注册表 + 单测 → 真机探活钉死 B/C。
2. guanlan:datafeed 别名接线 + 对账测。
3. guanlan:`fundflow/` 后端(sources → pulse → api)+ 单测。
4. server 接线 + opt-in poller。
5. 前端:外壳 + data 层 + 组件树(BreadthStrip → MarketFlowBars → IntradayChart → BoardRankTable)。
6. nav 页签 + `?v` bump。
7. ww_fundflow + 白名单四处同步 + 信封测。
8. 真机端到端验(9998)+ 全量回归 + 浏览器核对。
