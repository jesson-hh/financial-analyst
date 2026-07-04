# P5 · 选股池再打分(产业链 + 新闻情绪)— 设计文档

日期:2026-07-04 · 状态:已获用户批准(四决策 + A案 + 七节设计)
上游:P0-P4 全合 main(P4 至 `e5b1d8a`);industry AI 产业链看板已交付(build_board/框架票池);news_pulse 共享核心在产。

## 0. 目标与决策记录

**目标**(用户愿景第二阶段):在数据选股(v4/因子)之上,用**产业链逻辑分析**与**新闻情绪分析**对选股池**再打分**——展示型 overlay(不动真实选股信号),逐次落档,为 P6(经验自迭代扩到产业链/情绪/风格)供原料。

**用户拍板的四决策**(2026-07-03):
1. **接入形态:先展示后信号两步走**。P5 只做展示列 + 档案,零行为变化;信号混入(opt-in blend)待前向观察证据后另立项。
2. **产业链分口径:研报证据分 + 量价温度合成**。股票→链环映射取 ai_chain.yaml 票池;一票多环取最强环带环名;不在链上 → 诚实 null。
3. **情绪分:LLM 逐票限 TopN + 当日缓存**。只对再打分池(默认 v4 Top50)真调 judge_sentiment(deepseek 默认口径);按 date+code 缓存当日复用;无新闻/调用失败 → 该票 null 显形。
4. **触发:手动按钮 + 帷幄工具**。选股页「再打分」按钮 + `ww_rescore`(confirm)/`ww_rescore_view`;无定时器、无 env 开关。

**架构 A案(获批)**:新薄模块 `guanlan_v2/screen/rescore.py`(纯函数 + 单飞状态机),不喂已 1400+ 行的 screen/api.py,不挂 industry 下(再打分是选股域,产业链只是原料)。

**关键工程事实(已核)**:`ww_news_search` 生产上即在工具线程 `asyncio.run(news_sentiment(...))`(screen/llm._call_llm_json 不绑事件循环)——rescore daemon 线程同法直调,**无需 HTTP 自调**。

**红线(贯穿)**:不动 v4 排名/picks/blend;逐票失败逐票 null 绝不编数;LLM 成本显形(llm_calls/cache_hits);合并零行为变化;UI 只填充;无自改代码。

## 1. 打分引擎 `guanlan_v2/screen/rescore.py`(新)

### 1.1 产业链分(纯函数,零 LLM)

```python
def industry_scores(codes: list[str]) -> dict[str, dict | None]
# 返回 {code: {"seg": id, "seg_name": str, "chain": float,        # [-1,+1] 有界合成分
#              "research": float|None, "therm": float|None,       # 两成分原值(显形)
#              "quadrant": "hh|hl|lh|ll"} | None}                  # 不在链上 → None
```

- 数据源:`industry.aggregate.build_board()`(自带 TTL 缓存;`ok:false` → 整体诚实失败,不逐票编 None 冒充"不在链上")。
- 股票→链环:遍历 framework segments 的票池(stocks[].code,代码规格化对齐 `_stock_rows` 现行三形态匹配);一票多环取 `chain` 最强环。
- `chain` 合成:`research.score` 归一到 [-1,1](按 board 现值域,plan 阶段以真数据钉死归一常数)与 `therm`(0-100 → [-1,1] 线性)**等权平均**;单成分缺 → 用另一成分,两者全缺 → 该环不参与最强环竞选。
- board freshness 块(quote_date/last_ingest_at)原样透传进 run 档案与响应(陈旧显形,不拦)。

### 1.2 情绪分(LLM,限池 + 当日缓存)

```python
def news_scores(codes: list[str], top_n: int = 50) -> tuple[dict[str, dict | None], dict]
# 返回 ({code: {"tag": str, "read": str, "score": float} | None}, stats)
# stats = {"llm_calls": int, "cache_hits": int, "as_of": str, "market_read": str|None, "market_tilt": ...}
```

- 只取 codes 前 top_n 只;经 `asyncio.run(screen.news.news_sentiment(codes_batch, ...))`(仓内已验模式,daemon 线程内安全)。
- tag→score 映射:plan 阶段按 judge_sentiment 实际 tag 枚举钉死(正/中/负三档形,以真实枚举为准);无 tag/失败 → None。
- **当日缓存**:`var/rescore_news_cache.jsonl` 按 (date, code) keyed append;发起时先查同日缓存,命中不重调;stats 记 llm_calls/cache_hits 显形成本。
- market_read/market_tilt(大盘倾向)作 run 级元数据落档(不逐票进分)。

### 1.3 综合分(展示型)

```python
def composite_score(v4_pct: float | None, chain: float | None, news: float | None) -> dict
# → {"score": float|None, "parts": int}   # parts = 参与成分数(0-3);parts==0 → score None
```

- `score = mean(有值成分)`,成分先各自归一到 [-1,1](v4_pct: 0-100 → [-1,1]);**缺成分不补零不编数**——parts 徽章显形"n/3 成分"。
- 纯展示排序参考;绝不回写 v4/picks。

### 1.4 状态机 + 档案

- 单飞状态机照 `research/api.py` 范式(`_RESCORE_LOCK`/`_RESCORE_STATE`,daemon 线程,finally 清 running,progress lines)。
- run 档案 `var/rescore_runs.jsonl` append-only:`{run_id: "rs_"+hex10, ts, note, top_n, pool: [codes], rows: [{code, name, v4pct, chain: {...}|None, news: {...}|None, composite, parts}], stats: {llm_calls, cache_hits, board_freshness}, ok, error}`。**P6 经验迭代的原料。**
- 池来源:当前 v4 ranking 前 top_n(与选股页同一数据源;v4 不可用 → 诚实失败拒开跑)。

## 2. 端点(3 个,挂 screen 路由)

- `POST /screen/rescore` `{top_n?: int=50, note?: str}` → 抢单飞锁起 daemon 线程;已在跑 → `{ok:false, reason:"already_running", state}`。top_n 钳 [5, 100]。
- `GET /screen/rescore/status` → `{ok, state}`(running/phase/label/lines/elapsed)。
- `GET /screen/rescore/latest` → 最新一条 run 档案(无档案 → `{ok:true, run:null}` 诚实空态)。
- 诚实失败一律 `{ok:false, reason}` HTTP 200;无新 env 开关、无定时器、无子进程。

## 3. UI 填充(选股页,零重建)

- 结果区加「再打分」按钮(触发 POST + 轮询 status;running 态按钮显进度);完成拉 latest。
- 结果表逐行新列:**链环名·链环分**(不在链 → `—`)/ **情绪 tag**(null → `—`)/ **综合分 + n/3 徽章**。列只在有 latest 档案时渲染(无档案不占版面,诚实空态)。
- run 级元数据行:as_of / llm_calls / cache_hits / board freshness(陈旧显形)。
- 改 jsx 必 Edit bump 选股 html `?v=`。

## 4. 帷幄两工具(四处同步)

- `ww_rescore`(**confirm 门**,timing=minutes):发起 + 轮询到 done → 回成绩单摘要(top 数行 + 成本行);已在跑 → 如实回 state。
- `ww_rescore_view`(instant,只读):读 latest → 成绩单(逐票 top 行 + 成本 + freshness)。
- 四处同步:WW_TOOL_TABLE / `_SYSTEM_PROMPT`(能力行 + 纪律:再打分是展示参考,绝不改选股信号)/ test_console_tools(计数 + expected-endpoints)/ test_guanlan_mcp ×3。计数 **44→46 ww / 69→71 console / 48→50 MCP**;glmcp README 两处"N 个"同步。

## 5. 诚实合约汇总

- board 不可用 → run 整体 `ok:false`(绝不把"板子坏了"伪装成"全池不在链上")。
- 逐票:链外 null / 无新闻 null / LLM 失败 null,composite 按 parts 显形。
- 成本:llm_calls/cache_hits 进档案、工具回话、UI 元数据行。
- 产业链覆盖面=AI 链票池(现状唯一框架),池外票链环分全 null 是合法常态,UI/工具文案不装作有分。
- 展示型红线:本期产物**不进入**任何选股信号、picks、blend、seats 通路。

## 6. 测试计划

1. 引擎单测(新 `tests/test_screen_rescore.py`):industry_scores(多环取最强/链外 None/board fail 整体拒)/news_scores(缓存命中不重调/失败 None/stats 计数)/composite(parts 0-3 各态)——全部打桩 build_board 与 news_sentiment,零网络零引擎。
2. 端点三态 + 单飞锁(照 test_research_api 范式)。
3. 帷幄工具:四处同步计数 46/71/50 守护 + 两 impl 单测(打桩自调桥)。
4. 真机 e2e@9998(亲手,不转包):真 board + 真 LLM `top_n=5` 控成本 → 档案落盘逐票分项可见 → 选股页按钮全链 → 二次发起验缓存命中 → 还原(缓存/档案保留=真实历史,9999 收尾重启)。

## 7. 展望(本期不做,记档)

- **信号混入(两步走第二步)**:composite 经 opt-in 权重进 blend——门槛=前向观察证据(rescore 档案 vs 实际收益对照,P1 basket_perf 口径)。
- **P6 经验自迭代扩面**:rescore 档案 → keyed 教训(产业链环判断/情绪准确率/风格环境),单独 spec。
- 多产业链框架(现仅 AI 链)、定时再打分(job runner 议题)均未立项。
