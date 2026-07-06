# 观澜 · 全球情绪温度计 设计文档

日期:2026-07-06
状态:已批准(用户四问定案 + 双半球修订版口头批准"认可,开干")
灵感来源:Simon林_「Claude Code 手搓系列 4/7」(BV1RzEx6jEch)——投研看板接入 PolyMarket 和 Kalshi 概率数据,观测全球宏观预期概率。

## 1. 目标

用**预测市场真金白银交易出来的概率**观测全球宏观预期,与 **A 股本土投机情绪**并排对照,构成一页"全球情绪温度计"看板;同时给帷幄 agent 一个查询工具,研判时可引用全球预期。

**诚实红线**:纯展示层(与 P5 rescore/P6 rerank 同族),绝不混入任何交易信号;数据缺席/源失败诚实显形,不编造。

## 2. 已实测前提(2026-07-06 真机)

- `gamma-api.polymarket.com`(Gamma API)与 `api.elections.kalshi.com`(trade-api/v2)本机直连均 200,公开行情**免鉴权免费**,无需代理。
- Polymarket 按成交量排序全是体育/电竞,**必须**按宏观 `tag_slug` 过滤;实测可用标签:`economy` `fed-rates` `geopolitics` `inflation` `recession` `china` `crypto`(全部有活跃市场,含 Fed 决议/降息次数/台海/日本衰退等)。
- Polymarket 概率字段:market 的 `outcomes`/`outcomePrices` 1:1 平行数组(JSON 字符串),价格即隐含概率。
- Kalshi 新版字段为美元字符串:`last_price_dollars` / `yes_bid_dollars` / `no_bid_dollars` / `previous_price_dollars` 等;`series_ticker=KXFED` 可取联储利率系列;部分市场无成交时价格字段缺席(须容忍)。
- 限流:Gamma /events 500 req/10s、/markets 300 req/10s——我们每次刷新只发个位数请求,远低于限。
- A 股侧:观澜已有 `live_text_impl`(guanlan_v2/console/tools.py)经子进程跑 `G:\stocks\scripts\probe_live_text_sources.py`(a-stock-data 血统 13 端点),涨停/炸板/跌停/一字板池、热门概念、热榜均可现拉——**零新集成**。

## 3. 架构(双半球)

新后端模块 `guanlan_v2/macro/`(照 industry 薄壳先例),新前端页 `ui/macro/观澜 · 全球情绪.html`。

```
ui/macro/观澜 · 全球情绪.html ──> GET /macro/pulse ──> pulse.build_pulse()
                                  GET /macro/history          │
                                                    ┌─────────┴─────────┐
                                              sources.py            astock.py
                                          (PM Gamma + Kalshi)   (live_text_impl 复用)
                                                    │                   │
                                              themes.yaml         打板温度算术
                                                    └────> snapshots.jsonl(顺手落快照)
```

### 3.1 `themes.yaml` — 唯一事实源

主题清单(经用户选定,全都要):

| theme id | 名称 | polymarket tag_slug | kalshi series |
|---|---|---|---|
| fed | 美联储 · 利率 | fed-rates | KXFED 等 |
| inflation_recession | 通胀 · 衰退 | inflation, recession | (CPI 系列可后补) |
| geopolitics | 地缘政治 | geopolitics | — |
| china | 中美 · 中国 | china | — |
| crypto_risk | 加密 · 风险偏好 | crypto | — |

每主题字段:`label`、`polymarket_tags: []`、`kalshi_series: []`、`anchors: []`。
锚定市场(anchor)字段:`match`(question 子串或 ticker)、`direction`(+1=概率升=risk-on / -1=概率升=risk-off)、`weight`。**只有锚定市场参与温度合成**,其余市场只展示——避免自动猜方向的软伪造。

### 3.2 `sources.py` — 双源客户端

- `fetch_polymarket(tags, limit) -> list[MacroMarket]`:GET `/events?tag_slug=&active=true&closed=false&order=volume24hr&ascending=false&limit=`,每 event 取头部 markets,解析 outcomes/outcomePrices 取 Yes 概率(二元)或列多结果 top。
- `fetch_kalshi(series, limit) -> list[MacroMarket]`:GET `/trade-api/v2/markets?series_ticker=&status=open`,概率= `last_price_dollars`(缺则 `(yes_bid+yes_ask)/2`,再缺则跳过该市场并计 note)。
- 统一 schema `MacroMarket`:`{source: "polymarket"|"kalshi", id, question, prob, volume, liquidity, close_time, url, theme}`。
- 每源独立 try/except:单源失败→该源空列表+`notes` 记因(与 ww_news_live 同约:外部失败 ok:True+空+note,绝不编造)。timeout 10s,requests 直连。

### 3.3 `pulse.py` — 聚合、温度、快照

`build_pulse(refresh: bool) -> dict`:

1. 读 themes.yaml,逐主题拉双源(串行,总请求约 6-10 个);
2. 主题内按 volume 降序取 top N(默认 8)展示;
3. **全球温度**:锚定市场匹配(match 子串命中 question/ticker)→ `temp_theme = 50 + 50·Σ(w·dir·(prob-0.5))/Σw`,clamp 0-100;主题卡标注参与锚点数,0 锚点→温度显示 "—";总温度=有锚主题的等权均值;
4. **顺手落快照**:`var/macro_pulse/snapshots.jsonl` append 一行 `{ts, markets: [{source,id,prob}], temps: {...}}`(仅 refresh 真拉成功时写;读失败不写);
5. **Δ 计算**:从快照文件读同 id 市场最近一条 ≥20h 前的记录算 Δ24h;无历史→Δ=null 前端显示 "—"(头几天诚实无 Δ);
6. 非 refresh 请求:直接回最近一次快照(带 `stale_minutes` 显形),无快照则强制现拉。

`load_history(theme|market_id) -> list`:从 snapshots.jsonl 读概率序列供前端画曲线。快照文件按行 JSON,单文件即可(每天 ~几十行,数年不过百 MB;超限归档留待挂账)。

### 3.4 `astock.py` — A 股本土温度

复用 `guanlan_v2.console.tools.live_text_impl`(子进程 probe,只读):

- 首选端点:`em_zt_pool`(涨停池,投机广度;date 缺省当日)、`ths_hot_list`(热榜)、`ths_hot_reason`(涨停题材)——**实现期以 probe catalog 实测行结构为准**。
- **打板温度**(确定性算术,无 LLM):`温度 = clamp(50 + (涨停数-跌停数)·k1 - 炸板率·k2, 0, 100)`,k1/k2 常数进 themes.yaml `astock` 节;各输入计数与来源徽章一并返回,可核。
- probe 不可达/G:\stocks 缺席→`astock.available=false` + note,前端整侧显示"stocks 缺席·本土温度不可用"。

### 3.5 `api.py` + server 挂载

照 industry 先例:`build_macro_router()`,路由自带 `/macro/` 前缀,`asyncio.to_thread` 包同步实现(协程内严禁同步 HTTP——历史坑);`guanlan_v2/server.py` `include_router`(与 industry 挂载点相邻)。端点:

- `GET /macro/pulse?refresh=0|1` → 完整双半球 payload
- `GET /macro/history?market_id=|theme=` → 概率时间序列

### 3.6 前端 `ui/macro/`

照 industry 页三件套:`观澜 · 全球情绪.html` + `macro-data.jsx`(fetch 封装)+ `macro-app.jsx`(React UMD + babel standalone,gl-ds.css,guanlan-nav.js)。

布局:
- 顶部横幅:**总温度计**(全球预期温度 + A股本土温度双仪表,数字+色带)+ 数据新鲜度徽章(拉取时刻/stale)+ 刷新按钮(带 loading);
- 左半球:主题卡片矩阵——每卡:主题温度、top 市场列表(问题、概率横条、Δ24h 徽章、来源徽章 PM/K、截止日)、锚点计数;点击市场展开:历史概率曲线(快照)+ 原市场链接;
- 右半球:A 股本土温度——涨停/炸板/跌停计数卡 + 打板温度 + 涨停题材 top + 热榜 top;
- 诚实徽章:任一源失败在对应区块显示灰色降级条(note 原文)。

导航:`ui/_shared/guanlan-nav.js` MODULES 加 `{ label: '全球情绪', file: '../macro/观澜 · 全球情绪.html' }`,各页 html 的 `guanlan-nav.js?v=3` bump 至 `?v=4`(用 Edit 逐一改,历史坑:bump 必须动 ?v)。

### 3.7 帷幄工具 `ww_macro_pulse`

`console/tools.py` 加 `macro_pulse_impl()`:调 `pulse.build_pulse(refresh=True)`,content 组装双侧摘要(总温度、各主题温度+top3 市场概率、A股计数)。**四处同步**:specs 工具文档、`CONSOLE_ALLOWED` 白名单、`_SYSTEM_PROMPT` 工具清单、守护计数测试(现值以仓内实测为准,49/74/53 → +1)。

## 4. 错误处理总表

| 故障 | 行为 |
|---|---|
| PM/Kalshi 单源超时/非200 | 该源空 + notes 记因,另一源照常 |
| 双源全失败 | 回最近快照 + stale 显形;无快照→空态页(不编造) |
| Kalshi 市场无价 | 跳过该市场,计入 note |
| stocks probe 缺席/超时 | astock.available=false,右半球降级条 |
| 快照文件损坏行 | 逐行解析跳脏行(append-only jsonl 惯例) |
| 锚定市场全部未命中 | 主题温度 "—",卡片标"0 锚点" |

## 5. 测试策略

全 mock,绝不打真 API(历史教训:API 测试泄漏):

- `tests/test_macro_sources.py`:PM/Kalshi 夹具 JSON→归一 schema 断言;单源失败降级;Kalshi 无价跳过。
- `tests/test_macro_pulse.py`:温度算术(方向/权重/clamp/0锚点);快照 append+Δ 读回;stale 回退。
- `tests/test_macro_astock.py`:mock live_text_impl,打板温度算术,probe 缺席降级。
- router 冒烟(TestClient);守护计数测试同步。

## 6. 明确不做(YAGNI)

- 定时采集任务(现拉+顺手快照已够;job runner 未立项)
- 任何信号混入选股/落子(红线)
- Kalshi 鉴权接口/下单(只读公开行情)
- 快照文件轮转归档(量级数年不成问题,挂账)

## 7. 交付物清单

- `guanlan_v2/macro/{__init__,api,sources,pulse,astock}.py` + `themes.yaml`
- `guanlan_v2/server.py` 挂载(1 行族)
- `ui/macro/观澜 · 全球情绪.html` + `macro-{data,app}.jsx`;`ui/_shared/guanlan-nav.js` 加项 + 各页 ?v bump
- `console/tools.py` ww_macro_pulse + 四处同步
- tests 三件 + 计数同步
