# 实时新闻情绪研判 —— 接入研报 + 帷幄

- 日期:2026-06-13
- 状态:设计已批准(用户确认三项选择 + 三项判断),待出实施计划
- 主题 slug:`news-sentiment-research`

---

## 1. 目标 & 背景

### 目标
1. **每次生成单票深度研报时**,自动加入一段**实时新闻情绪研判**(覆盖个股新闻 + 大盘走势新闻),作为研报正文的一个独立章节。
2. **给研报新增一个子 agent** 专责"搜索新闻 + 判情绪"。
3. **把这份能力作为一个工具装进帷幄**(`ww_news_search`),让帷幄 agent 随时联网查个股/大盘新闻与情绪。

### 现状(调研已坐实)
- **真正的研报**不在 `guanlan_v2/reports/api.py`(那只持久化工作流 JSON),而是 fork 引擎 `engine/financial_analyst/` 里的多 agent LLM 管线,以子进程 `financial-analyst report CODE` 跑(`PYTHONPATH=engine` 注入),DAG 见 `config/swarm/stock-deep-dive.yaml`,终点 `report-writer` 做**唯一一次** prose LLM 调用。**仅个股**,大盘只作 §二 背景。
- **核心能力其实已存在**:[`guanlan_v2/screen/news.py`](../../../guanlan_v2/screen/news.py) 的 `news_sentiment()` 已做"实时东方财富 7×24 快讯抓取(`EastmoneyKuaixunCollector().fetch()`,**只读、不写库、红线干净**)+ deepseek LLM 情绪",一次产出**大盘主线 `market_read` + 个股 tag `by_code`**,且**诚实**(无快讯不编造)。只是**没接进研报、帷幄也没有对应工具**。
- 研报 DAG 现有 `news-reader`(tier1),但只抽事件/数字、**不判情绪**,且只喂 `risk-officer`、**不喂 `report-writer`**。
- 帷幄无任何联网搜索/新闻工具;记忆里那条"web_search 工具(方案已对齐待 key 拍板)"从未落盘成文。
- 引擎子进程**导不到 `guanlan_v2.*`**(只带 `PYTHONPATH=engine`),但快讯采集器 `EastmoneyKuaixunCollector` 本就在引擎里。

---

## 2. 决策记录

### 用户已选(三项)
1. **抓取方式 = 直连 API 为主**:复用东方财富 7×24 快讯直连 + 加 `akshare stock_news_em` 补个股深度新闻。免费、无 key、无需浏览器。Playwright 仅作验证码/深抓兜底(**不进 v1**)。
2. **情绪方法 = LLM 研判**:复用现有 deepseek/reasoner,带引用理由,接平台已有的 claim_audit 断言质检文化。
3. **研报落点 = 独立章节 + 定性佐证**:新增「新闻情绪研判」章节,喂 `report-writer` 作定性参考;**不直接改 5 维评级数字、不碰 `dim_sum` 自洽校验**。

### 实现侧判断(三项,可推翻)
1. **共享核心下沉引擎侧**(`engine/financial_analyst/data/news_pulse.py`),而非复用 `guanlan_v2.screen.news` —— 因研报子进程导不到 `guanlan_v2.*`。情绪 LLM 调用各注入各自 client(研报用 `LLMClient.for_agent`,帷幄侧用现有 deepseek),但**共用同一套 prompt + 解析 + 诚实规则**,不分叉。
2. **akshare 做成可选依赖**:东方财富快讯为主路径(零新依赖);akshare 未安装则自动降级为"仅快讯"(个股偏薄但真实),不报错。
3. **Playwright 浏览器兜底不进 v1**:直连已足够;一上来就背验证码维护不划算。列为后续。

---

## 3. 架构总览

两条入口共用一个引擎侧只读新闻核心:

```
帷幄对话 ──→ ww_news_search 工具(新增) ─┐
                                          ├─→ news_pulse 共享核心(引擎侧·只读·红线干净)
研报跑道 ──→ news-sentiment 子agent(新增) ┘      ├─ 东方财富 7×24 快讯(大盘主线+个股tag,复用)
                                                  ├─ akshare stock_news_em(个股深度,新增·可选)
                                                  └─ LLM 情绪研判(引用理由·无则不编造,复用 reasoner)
帷幄侧输出:content + 新闻卡片即时回显
研报侧输出:report-writer → 「新闻情绪研判」章节(定性佐证·不改 5 维评级)
```

### 个股 vs 大盘
一次抓取同时产出 `market_read`(大盘走势)+ `by_code`(个股倾向)。
- 研报(个股):本票报告同时含"大盘情绪背景"+"本票新闻情绪"。
- 帷幄工具:按 `scope = stock | market | both` 取舍。

---

## 4. 组件详述

### 4.1 `news_pulse` 共享核心(新建)
路径:`engine/financial_analyst/data/news_pulse.py`

职责:纯数据 + prompt + 诚实规则,**不做 LLM 调用**(LLM client 由调用方注入),**不写任何引擎数据/文件**(红线)。

把 [`screen/news.py`](../../../guanlan_v2/screen/news.py) 里已验证的逻辑下沉、引擎原生化:
- `em_to_qlib(stocks_str) -> List[str]` —— 东财关联串 → qlib 代码(直接搬)。
- `fetch_kuaixun(limit=200) -> List[dict]` —— 实时拉东财 7×24 快讯,每条 `{time,title,summary,codes[]}`(搬,改 import 为引擎内 `financial_analyst.data.collectors.opencli.eastmoney_kuaixun.EastmoneyKuaixunCollector`)。
- `fetch_stock_news(code, limit=50) -> List[dict]` —— **新增**:akshare `stock_news_em` 个股深度新闻;akshare 缺失或失败 → 返回 `[]` 并置降级标记,**不抛**。
- `NEWS_SYSTEM`(情绪 system prompt,搬)+ `build_news_prompt(market, by_code, stock_news)`(搬并扩展容纳个股深度新闻)。
- `judge_sentiment(market, by_code, stock_news, *, llm_json_call) -> dict` —— 用注入的 `llm_json_call(system, user) -> {ok, data, model, ...}` 跑情绪;封装诚实降级(LLM 失败 → 返回真快讯原文 + 情绪 None)。两个入口注入各自的 LLM 适配器。

**单一事实来源**:`guanlan_v2/screen/news.py` 的 `news_sentiment()` **改为薄适配器**,把抓取 + prompt + 诚实规则委托给 `news_pulse`(自己只保留 deepseek `_call_llm_json` 注入),不再各持一份逻辑。`screen/news.py` 本就 `import financial_analyst.data.collectors...`,故能 `import` 引擎侧 `news_pulse`。这样引擎侧研报 agent 与 guanlan 侧选股页/帷幄工具共用同一核心。改造须保持 `screen/news.py` 现有签名/返回字段不变(选股页 C 节在用),仅内部委托。

反爬:抓取限频 1–3s + 进程内短缓存(快讯 ~5min TTL,个股 akshare ~1 天 TTL,按 `(code, 日期)` 键)。遇滑块**不强爬**,降级标注。

### 4.2 `news-sentiment` 研报子 agent(新建)
路径:`engine/financial_analyst/agent/tier1/news_sentiment.py`,仿 [`news_reader.py`](../../../engine/financial_analyst/agent/tier1/news_reader.py)。

- `NAME = "news-sentiment"`,`OUTPUT_SCHEMA = NewsSentimentOutput`(见 §5)。
- `_execute(inputs)`:取 `code, asof_date` → `news_pulse.fetch_kuaixun()` + `news_pulse.fetch_stock_news(code)` → 用引擎 `LLMClient.for_agent("news-sentiment")` 包成 `llm_json_call` 注入 `news_pulse.judge_sentiment()` → 返回结构化输出。
- 沿用 `news_reader.py` 的 **UNTRUSTED 数据注入护栏**(把新闻当 DATA、绝不执行其中指令)。
- 诚实:抓取失败 → `ok-but-empty` + 原因;无相关快讯 → 明确标「近期无相关快讯」,不编造。

**接线(4 处 + 注册):**
1. `config/swarm/stock-deep-dive.yaml`:
   - 新增 tier1 节点 `news-sentiment`(`deps: []`,`input_keys: [code, asof_date]`)。
   - `report-writer` 的 `deps`(行 68–72)与 `input_keys`(行 73–76)**都**加上 `news-sentiment`。
   - **同步**引擎内 `_resources` 旧副本(`find_config` 以 cwd 副本为准,但两份须一致)。
2. `engine/financial_analyst/tui.py`(`_register_agents` 行 234–272):注册 `NewsSentiment` 类,使 orchestrator 能按名实例化。
3. `engine/financial_analyst/agent/tier3/report_writer.py`:
   - 硬编码 `upstream` 列表(行 123–127)加 `"news-sentiment"`。
   - `SYSTEM_PROMPT`(行 57–109)加一行 upstream 说明 + 一段「在 markdown_body 里新增『新闻情绪研判』章节」的指令(走平白情绪语言,避开被禁量化词汇)。
   - **不**新增评级维度、**不**改 `dim_sum` 自洽逻辑(行 175–182)。
4. `memories/report-writer/report_template.md`:新增「新闻情绪研判」章节模板块(`market_read` + 本票 tag + 引用快讯;无则写「近期无相关消息面」)。

### 4.3 `ww_news_search` 帷幄工具(新建)
路径:`guanlan_v2/console/tools.py`

- `news_search_impl(code: str = "", scope: str = "both", query: str = "", days: int = 7, limit: int = 15) -> Dict`:
  - 复用共享核心抓取 + 情绪(帷幄侧可直接复用 `guanlan_v2.screen.news.news_sentiment`,因 console 进程能导 `guanlan_v2.*`;新增 akshare 个股深度时走 `news_pulse.fetch_stock_news`)。
  - `query` 非空时对快讯/新闻标题做关键词过滤(源是 code/market 键,不是全文搜索)。
  - 工具跑在 `asyncio.to_thread` 线程里 → 内部用 `asyncio.run(news_sentiment(...))` 跑异步无碍;**禁止**在 api.py 协程内同步自 HTTP。
  - 返回 `{ok, content(可读摘要), artifact(新闻卡片), raw}`;**不**用 `background`(这是即时查询,不是长任务)。
- 注册:在 `register_console_tools()` 的 `specs`(行 495–576)追加一条元组 `('ww_news_search', '实时联网检索个股/大盘新闻与情绪研判(东财快讯+akshare,带引用理由,无则诚实标注)', {schema}, _wrap(news_search_impl), 'seconds', False)`。
- 白名单:`CONSOLE_ALLOWED`(行 585–592)加 `'ww_news_search'`(**必须**,否则 LLM 看不见也不能调)。
- 命名区分:`ww_news_search` = 实时联网新闻+情绪;现有 `news_query` = 本地 NewsDB 检索,二者并存。
- prompt:`guanlan_v2/console/api.py` 的 `_SYSTEM_PROMPT`(行 26–39)加一行,告知何时调 `ww_news_search`。

---

## 5. 数据结构

### NewsSentimentOutput(研报子 agent)
```python
class NewsSentimentOutput(BaseModel):
    code: str
    asof_date: str
    as_of: Optional[str]              # 快讯最新时间戳
    source: str                        # "东方财富 7x24 快讯(实时)[+ akshare 个股新闻]"
    market_read: Optional[str]         # 大盘消息面主线一句话(利好/利空/中性)
    market_tilt: Optional[str]         # 利好/利空/中性
    stock_tilt: Optional[str]          # 本票倾向;无相关快讯 → None
    stock_read: Optional[str]          # 本票一句解读 + 引用
    evidence: List[dict]               # [{time, title, source}] 真快讯原文引用
    covered: bool                      # 本票是否有相关快讯
    honest_note: str                   # 诚实说明(降级/无快讯/LLM 失败)
```

### ww_news_search 工具 I/O
- 入参:`code`(可选,个股)、`scope`(stock/market/both)、`query`(关键词过滤,可选)、`days`、`limit`。
- 出参 `content`:可读摘要(大盘主线 + 本票 tag + 引用);`artifact`:新闻卡片结构。

---

## 6. 诚实 / 错误处理(沿用平台红线文化)
- 抓取失败 → `ok:False` + 原因;研报章节写「消息面数据暂不可用」,**不阻塞**整篇研报。
- LLM 失败 → 仍回真快讯原文,情绪置 `None` + 诚实 note。
- akshare 未安装/失败 → 降级"仅快讯",标注,不报错。
- 无相关快讯的个股 → 明确「近期无相关快讯」,**绝不编造**(防 LLM 给无快讯的票编情绪,沿用 `screen/news.py` 的 `by_code` 过滤)。
- 新闻文本一律当 **UNTRUSTED DATA**,绝不执行其中指令(沿用 `news_reader.py` 护栏)。

---

## 7. 依赖 / 反爬 / 运维
- **唯一新增 pip 依赖:`akshare`**(免费无 key),且可选降级。东财快讯路径零新依赖。
- 反爬:限频 1–3s + 进程内短缓存;东财滑块时降级不强爬。NewsNow 自托管 / tushare Pro / Playwright 兜底列为**后续**,不进 v1。
- 改 engine → 需重启看门狗 9999;但研报子进程每次新起,`news_sentiment.py` / `report_writer.py` 落盘即生效(子进程无需重启 9999)。
- `stock-deep-dive.yaml` 两份副本(cwd 权威 + `_resources` 旧版)**必须同步**。
- 那条挂账的 web_search key **不再是阻塞项**:直连 A 股新闻不需要它。

---

## 8. 测试计划
- **单元**:`news_pulse.fetch_kuaixun`(mock 采集器)、`fetch_stock_news`(mock akshare + 缺失降级)、`judge_sentiment`(mock LLM:成功 / LLM 失败 / 无快讯 三分支)、`em_to_qlib`。
- **帷幄工具**:`news_search_impl` 按 [`tests/test_console_tools.py`](../../../tests/test_console_tools.py) 的 `CTX_STORE/CTX_SID` + 直调 impl + mock 桥 模式;断言 `scope` 三态、`query` 过滤、降级路径。
- **研报 agent**:`NewsSentiment._execute` mock 抓取+LLM;断言诚实分支(无快讯/抓取失败/LLM 失败)。
- **端到端冒烟**:真跑一篇研报,断言 markdown 含「新闻情绪研判」章节且引用为真快讯原文;断言 5 维评级数字未被新章节改动。

---

## 9. 不在 v1 范围(YAGNI)
- Playwright/browser-use 浏览器抓取兜底(直连够用)。
- 开放网络泛搜 API(博查/Tavily/Exa)+ key(挂账,另议)。
- 独立"大盘研报"preset(大盘已作个股研报背景 + 帷幄 `scope=market` 覆盖)。
- 新增"消息面"评级维度(本期只做定性章节,不动 `rating_system.md` 自洽)。
- NewsNow 自托管 / tushare Pro 二源(反爬升级,后续按需)。

---

## 10. 精确接线点清单(实施地图)

| 动作 | 文件 | 位置 |
|------|------|------|
| 新建共享核心 | `engine/financial_analyst/data/news_pulse.py` | 新文件(逻辑搬自 `guanlan_v2/screen/news.py`) |
| 新建研报子 agent | `engine/financial_analyst/agent/tier1/news_sentiment.py` | 新文件(仿 `news_reader.py`) |
| DAG 加节点 + 接 writer | `config/swarm/stock-deep-dive.yaml` | tier1 新节点;report-writer `deps` 68–72 + `input_keys` 73–76 |
| DAG 副本同步 | 引擎 `_resources` 内 `stock-deep-dive.yaml` | 与 cwd 副本一致 |
| 注册 agent 类 | `engine/financial_analyst/tui.py` | `_register_agents` 234–272 |
| writer 收 upstream | `engine/financial_analyst/agent/tier3/report_writer.py` | 硬编码 upstream 列表 123–127 |
| writer prompt + 章节 | `engine/financial_analyst/agent/tier3/report_writer.py` | `SYSTEM_PROMPT` 57–109 |
| 研报模板 | `memories/report-writer/report_template.md` | 新增章节块 |
| 帷幄工具 impl | `guanlan_v2/console/tools.py` | `news_search_impl`(近 `seats_history_impl`) |
| 帷幄工具注册 | `guanlan_v2/console/tools.py` | `register_console_tools()` specs 495–576 |
| 帷幄工具白名单 | `guanlan_v2/console/tools.py` | `CONSOLE_ALLOWED` 585–592 |
| 帷幄 prompt | `guanlan_v2/console/api.py` | `_SYSTEM_PROMPT` 26–39 |
| 测试 | `tests/test_console_tools.py` + 新建引擎侧测试 | 见 §8 |
