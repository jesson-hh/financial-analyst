# 帷幄 + 个股研报 F10 语料富化 — 设计文档

- 日期:2026-06-16
- 触发:铜陵有色 SZ000630 研报缺陷诊断(见记忆 `stock-report-pipeline-defects`)+ 找到数据源 `tdx-tq-bridge` skill
- 范围:全 F10 富化(市值/估值 + 事件 + 龙虎榜/融资融券/股东 + 券商评级目标价)、严格逐行 PIT、帷幄 `ww_f10` 工具
- 用户红线:**准确、不要幻觉、信息量大**;**回测能生成"过去的研报"且不看到决策日之后的消息**

---

## 1. 背景与问题

个股深度研报(`config/swarm/stock-deep-dive.yaml`,17 agent)当前对个股真数据"取得到却用不上",已确诊缺陷见记忆 `stock-report-pipeline-defects`。其中三条由本设计修复:

- **①消息面跑题**:本票消息面塞无关大盘头条,`news_covered=false`。
- **②市值/估值误判**:`quote_fetcher.py:64` daily_basic 只回看 5 天,daily_basic 滞后行情 4 交易日 → 窗口空 → PE/PB/MV 全 null → LLM 瞎猜 Tier:Small → 整条"游资博弈票"看空主线建在伪前提上。
- **③个股 fetcher 死路**:`f10_reader.py:52-53` 被 `f10_root is None` 短路,`swarm/loader.py:92` 从不传 root。

数据源已实测验真:`G:\stocks` 下的 **TDX F10 本地语料**(skill `tdx-tq-bridge`):
- 索引 `G:\stocks\stock_data\parquet\tdx_f10_index.parquet`:71336 行 / **5303 只股** / 15 类 / **UTF-8** / 快照日 20260418..20260610。
- 列:`code`(如 `SZ000630`)、`category`、`date`(`yyyymmdd` 快照日)、`length`、`hash`、`content_path`、`updated_at`。
- 原文 `G:\stocks\news_data\tdx_f10\{code小写}\{category}_{yyyymmdd}.txt`。
- 15 类:最新提示、公司大事、研究报告、龙虎榜单、主力追踪、业内点评、股东研究、财务分析、资本运作、分红扩股、高层治理、经营分析、行业分析、公司概况、股本结构。

实测 000630:`最新提示` 有总股本 134.0947亿/每股净资产 2.7954/ROE 3.59%/营收 646.99亿(+83.59%);`公司大事` 有真本票事件(05-29 权益分派/05-21 董事会/05-14 股东大会);`研究报告` 有券商评级 + 目标价(国泰海通 报告价 5.81→目标价 6.80);`龙虎榜单` 有融资融券/资金流向/大宗交易日明细到 05-29。

## 2. 目标 / 非目标

**目标**
- 一个确定性 F10 解析层,把语料解析成结构化事实,统一做 PIT。
- 修复 ①②③,并新增"券商评级与目标价"研报段。
- 帷幄新增 `ww_f10` 工具,可按 code/category/asof 查结构化 F10。
- 全程**确定性抽取数字/日期**,LLM 不碰数字;诚实降级,绝不伪造。

**非目标(本轮不做)**
- TQ 实时桥(`tdx_tq_probe.py`):需 TDX 客户端开着+登录,headless/scheduler 不可靠 → 留作手动/opt-in,本轮不接自动管线。
- 盘中实时新闻(akshare `stock_news_em`):已在 news-sentiment 路径,本设计与之**互补并存**,不替换。
- 语料定期重采集(staleness 根治):本轮只读现有快照 + 暴露新鲜度,采集调度独立项目。
- 全市场/批量预算化 F10(每股一次按需读)。

## 3. 架构总览

仿现有共享核心 `data/news_pulse.py`(服务 3 消费方)模式,新增**一个**共享模块:

```
data/f10_corpus.py   ← 唯一读语料 + 解析 + PIT 的地方
        │  load_facts(code, asof=None) -> F10Facts
        ├─ quote-fetcher / fundamental-analyst   (市值/估值兜底)   → 灭②
        ├─ news-sentiment                        (事件折进本票证据)→ 灭①
        ├─ f10-reader                            (龙虎榜/股东/事件)→ 灭③
        ├─ report-writer                         (券商评级+目标价段)→ 送C档
        └─ 帷幄 ww_f10 工具                       (按需结构化查询)
```

**关键架构决定**:PIT 与确定性解析**只写在 f10_corpus 一处**,所有消费方按构造即 PIT 正确、不幻觉。这避免了原始缺陷的成因——多处独立接线各自漂移出 bug。

**路径自解析(简化 ③ 修复)**:`f10_corpus` 自己从配置常量解析语料根,默认
`CORPUS_ROOT = G:\stocks\news_data\tdx_f10`、`INDEX_PATH = G:\stocks\stock_data\parquet\tdx_f10_index.parquet`,可经环境变量/配置覆盖。
因此 f10-reader 不再依赖 loader 线程化 `f10_root`——把 `if self.f10_root is None: return empty` 短路改成"调 f10_corpus"。**`swarm/loader.py` 零改动**(blast radius 最小)。模块同时接受可选 `root=`/`index_path=` 入参供测试与配置覆盖。

## 4. 数据模型(f10_corpus 公开面)

```python
# 公开入口
def locate(code: str, *, root=None, index_path=None) -> Snapshot | None
def load_facts(code: str, asof: str | None = None, *, root=None, index_path=None) -> F10Facts
```

`F10Facts`(dataclass,带 `.to_dict()` 供 JSON/帷幄):

| 字段 | 来源类目 | 说明 |
|---|---|---|
| `valuation` | 最新提示 | `{total_shares, float_shares, bvps, eps_by_period, roe, revenue, revenue_yoy, net_profit, net_profit_yoy, report_period}`,各值带报告期 |
| `events` | 公司大事 + 业内点评 + 最新提示公告 | `[{date, title, category}]`,按日期倒序 |
| `lhb` | 龙虎榜单 | `{margin:[{date,...}], moneyflow:[...], block_trades:[...], abnormal:[...]}` |
| `holders` | 股东研究 | 最近一期(可见日 ≤ asof) |
| `broker` | 研究报告 | `{ratings:[{date, org, rating, prev, report_price, target_price}]}`;滚动综合评级在回测中丢弃 |
| `provenance` | — | `[{category, snapshot_date, internal_dates_used}]` |
| `snapshot_date` | 索引 | 快照日(暴露新鲜度) |
| `asof` | 入参 | PIT 截止;None=live |
| `honest_note` | — | 如"asof 早于快照内容,F10 无可用料" |

**市值不在模块算**:模块只给 `total_shares`,消费方(quote-fetcher)用 `总股本 × 当前价` 算 → 职责单一、价在调用方手上。

## 5. PIT 机制(核心)

`asof` 一参三态:

**live(`asof=None`)**:用全量最新快照,含滚动聚合。

**回测(`asof="YYYY-MM-DD"`)**:
1. **事件流类**(公司大事/业内点评/龙虎榜/融资融券日表/研报记录):逐行解析内部日期,**保留 date ≤ asof**。
2. **滚动聚合类**(综合评级"近1/2/3月"指数、概念板块):相对快照日算、无法重建 → **整块丢弃**。
3. **季报财务**:用 **报告期 + 标准披露滞后** 作为"可见日"(Q1→04-30 / 半年→08-31 / Q3→10-31 / 年报→次年04-30),取可见日 ≤ asof 的最近一期。与仓内现有 vintage / regime_asof 纪律一致,防当季未披露看未来。
   - `total_shares`、`bvps`、`roe`、`revenue` 等按该可见报告期取值。
4. **全被裁空**(asof 早于快照内容)→ `honest_note` + 空 facts → 研报渲染"无"。

**诚实边界**(写入 provenance / honest_note,不隐藏):
- 语料是**单快照非历史时序**(每股一份,大多 06-01/06-07~10),无法重建任意过去日期的 F10 原貌;回测 PIT 靠**文件内日期过滤**实现,只对带日期的事件流类有效。
- 深历史回测(asof 早于快照最早内容,如 2025 年)→ F10 给不出料,诚实空。
- 这正满足用户"不看到最新消息":事件流逐行裁到 ≤ asof,滚动聚合丢弃。

## 6. 消费方接点

| 缺陷 | 文件 | 改法 |
|---|---|---|
| ②市值误判 | `agent/tier1/quote_fetcher.py` | `:83` 的 `if db…` 之外加 F10 兜底:db 空或缺字段时,用 `f10_corpus.load_facts(code, asof).valuation` 算 `mv_yi=总股本×price/1e8`、`pb=price/每股净资产`、ROE/营收/净利同比直填;`pe` 用 TTM(YTD 滚动:本期累计 + 上年全年 − 上年同期累计)best-effort,若 4 季分解不干净则置 `—`(宁缺不猜) |
| ②延伸 | `agent/tier2/fundamental_analyst.py` | 拿到真 `mv_yi` → 不再默认 mid 瞎猜(主要由 quote-fetcher 喂真值即解决,必要时显式读 valuation 字段) |
| ①跑题 | `agent/tier1/news_sentiment.py` | `:60-62` 把 PIT 后的 F10 events 折进 `by_code[code]` 证据,与 akshare 盘中头条**并存**;去掉 `:62` 本票无料回退大盘前6条快讯的逻辑(本票无料 → 诚实"无") |
| ③死路 | `agent/tier1/f10_reader.py` | `:52-53` 短路改为调 `f10_corpus.load_facts`(自解析路径);喂 risk-officer 的事件/龙虎榜/股东改吃**确定性结构化事实**,LLM 仅做事件分类(positive/negative/calendar);loader 不动 |
| 送C档 | `agent/tier3/report_writer.py` | 新增"券商评级与目标价"段(源 broker facts,逐字目标价);修 SYSTEM_PROMPT 里已删 factor-computer/model-predictor/quant 的陈述;修 `:113/:114` covered=false 自相矛盾(无料写"无",不逐字引用空证据) |

## 7. 帷幄 `ww_f10` 工具

- 签名:`ww_f10(code, category=None, asof=None, keyword=None)` → 调 `f10_corpus.load_facts` → 返结构化 JSON(events/valuation/lhb/broker/holders + provenance)。
- 带 `asof` 即可查历史口径(同 PIT 规则)。
- 注册进帷幄 ALLOWED 工具表,同步 console 工具计数与守护测试(参考既往 `ww_*` 工具新增模式)。
- 与现有 `ww_news_search`(akshare/快讯)**互补**:`ww_f10`=本票基本面/事件/龙虎榜/目标价骨架;`ww_news_search`=盘中头条。

## 8. 防幻觉合约

- 数字/日期/目标价/龙虎榜金额**全确定性正则抽取**(对定宽 ASCII 表),作为结构化事实**逐字渲染** + 挂出处。
- LLM 收到事实当 DATA(沿用 news_reader/news_sentiment 现有 untrusted 护栏),指令明确:**事实为准,禁编造数字/日期/目标价,缺则"无"**;LLM 只做叙事综合与情绪研判。
- 宁缺不猜:任何不能干净解析的字段置"无"/`—`,不回退到 LLM 估计。

## 9. 错误处理(诚实降级)

- 该股无 F10(`locate` 返 None)→ `honest_note` + 空 facts → 研报段渲染"无",不伪造。
- 单类目解析失败 → 跳过该类目并记 provenance note,不拖垮整份研报(承接 backend-audit soft_deps 精神)。
- 索引缺失/路径不可达(跨仓 `G:\stocks`)→ 优雅降级走现有数据源,记日志,研报继续。
- 新鲜度:provenance 暴露"F10 快照截至 X 日",研报诚实标注(语料是快照非实时流)。

## 10. 测试(TDD)

- **解析单测**:逐类目 parser 对 checked-in fixture 文件 → 抽出数字/日期/目标价与文件逐字一致。
- **PIT 单测**:给 asof,断言 post-asof 事件行被丢、滚动聚合被丢、季报按披露滞后取期;asof 过早 → 诚实空。
- **降级单测**:无料 / 解析失败 / 路径不可达 → 诚实空,不崩、不编造。
- **市值合约**:`mv_yi = 总股本 × price / 1e8`;无总股本 → None 不猜。
- **集成**:news-sentiment 折入 F10 events(本票有料则 covered=true 且证据是本票事件);quote-fetcher 在 db 空时 F10 兜底触发。
- **回归**:`tests/test_news_pulse.py` 全绿;研报全量 pytest 绿。
- fixture:从 000630 真文件裁一份小样本入 `tests/fixtures/f10/`(UTF-8),含已知数字便于断言。

## 11. 配置

- `f10_corpus` 内常量 `CORPUS_ROOT` / `INDEX_PATH`,默认指向 `G:\stocks`,可经环境变量(如 `GL_F10_ROOT` / `GL_F10_INDEX`)或配置覆盖。
- 跨仓:数据在 `G:\stocks`,引擎在 `G:\guanlan-v2`,默认绝对路径;非阻塞。

## 12. 待澄清 / 风险

- **PE-TTM 季度分解**:F10 每股收益疑为累计 YTD;TTM 滚动公式需对真数据核验,不干净则 `—`(已在 §6 定调宁缺不猜)。
- **披露滞后表**是近似(用标准截止日,非每股真实公告日);保守取整能防前视,代价是偶尔晚一两天用上某季报——可接受。
- **类目编码**:索引 `category` 为中文,解析按类目名分派 parser;需对 15 类逐一确认表结构(实现期逐类目验证)。
- **快照多版本**:743/5303 股有 2-3 个快照日。`locate` 选快照规则:**live 取最新快照;回测取 snapshot_date ≤ asof 的最新快照**;若该股全部快照日 > asof,则退化为只靠文件内行日期过滤(snapshot_date 仍暴露在 provenance,研报据此诚实标注快照时效)。

## 13. 落地顺序(供 writing-plans 展开)

1. `f10_corpus.py` 解析层 + PIT + 单测(地基)。
2. quote-fetcher 市值/估值兜底(灭②,收益最高最易验)。
3. news-sentiment 折入(灭①)。
4. f10-reader 复活 + 结构化(灭③)。
5. report-writer 券商评级段 + SYSTEM_PROMPT/契约修。
6. 帷幄 `ww_f10` 工具 + console 测试。
7. 端到端:重跑 000630 研报,对比改前(市值不再 null、消息面是本票事件、有目标价、回测 asof 不前视)。
