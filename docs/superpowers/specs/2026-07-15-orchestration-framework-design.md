# 编排框架 · Orchestrator-Workers + Evaluator-Optimizer — 设计文档

日期:2026-07-15 · 状态:已获用户批准(四轮决策 + Lane 0 市场情境层 + 经验库);待写实现计划(writing-plans)。

**参考来源**:
- HKUDS/Vibe-Trading — 静态 YAML DAG 编排(预设=依赖图)。
- TauricResearch/TradingAgents `@01477f9` — agent 名册、`dataflows/interface.py`(多源融合路由)、`agents/schemas.py`(结构化输出双表示)。
- simonlin1212/TradingAgents-astock `@e6b32a4` — A股 vendor 适配缝、3 个 A股原生 analyst、微结构提示词、`signal_data` 类。
- anthropics/financial-services `@4aa51ed` — `SKILL.md` 格式、`reader/critic/writer` 能力分档、不可信输入隔离、workers 不能再 spawn 子 agent。
- 既有帷幄:`workflow/executor.py`(`run_graph` 确定性图执行器)、`research/loop.py`(提案→求值→过门→批判 回路)、`autonomy/subagent.py`(`run_section_agent` worker 原语)、`console/curator.py`(记忆归档)、`seats/`(落子/PA)、`datafeed/`(数据层)、`macro/` `market_tape` `fundflow`(市场级数据)。

---

## 0. 目标与决策记录

**目标**:抽出一个通用编排内核 `guanlan_v2/orchestration/`,把帷幄现有零散的多 agent 能力(复盘官五段、bull/bear 报告链、research 回路、rescore/rerank)统一成两个可组合的工作流——**Orchestrator-Workers**(中央 LLM 动态把任务拆成 worker 子任务并汇聚)与 **Evaluator-Optimizer**(生成→确定性求值→评估反馈→改进 的有界迭代)。同一内核由**落子(回测 replay)**与**帷幄(live/研究)**共用,只换数据绑定。

**用户拍板的决策**:
1. **范围**:先抽 `guanlan_v2/orchestration/` 通用内核,落子/帷幄 各写 adapter 后接(而非先绑某一具体业务)。
2. **执行器归属**:新建**独立 LLM-worker runner**;确定性因子执行器 `workflow/executor.run_graph` 保持纯净不动,作为 worker/evaluator 调用的工具。
3. **反馈归因治理**:**确定性归因优先(边界门违反 + 引用链 + incomplete 节点)→ LLM 仅兜底**;责任 worker 自省(Reflexion)提炼教训;统一经**记忆 curator** 去重/校验/`[[链接]]`才入长期库。(避开 Who&When ICML'25 全自动归因 SOTA 仅 ~53% 的坑。)
4. **决定 1B · Lane D 出信号边界**:决策/风控车道**只出带 `非LLM信号` 徽章的 advisory 工件 + 影子组合(虚拟收益曲线,不动真钱/不进实盘信号)**;唯一执行器仍是落子 `runBacktest` 的 clock/止损止盈。影子组合的虚拟收益曲线专供 Evaluator-Optimizer 评估 LLM 决策质量。
5. **决定 2 · 辩论深度**:多空辩论 ≤2 轮、风控三席 ≤2 轮;仅 `dec.pm` 跑 `reasoner_deep`,辩手用 `fast`/`reasoner`(护住座席≤24 / token 闸)。
6. **决定 3 · 路由回落**:窄回落(仅 `RateLimitError`(及 `NotConfiguredError`)触发跨 vendor);同一 locale 的源冗余塞进 adapter 内部;A股独有的龙虎榜/北向/资金流/解禁单独成 `signal_data` 类并标 OPTIONAL(端点常坏 → 降级不 crash)。具体数据源获取实现**暂缓**,本期只定接口。
7. **决定 4 · 下游读法**:工件同时带结构化 `payload` + `rendered_md`,下游读 typed `payload`(不 regex 抠 markdown);现有 `reports/` 链分批迁进 `WorkerSpec`。
8. **Lane 0 市场情境层**:新增自上而下的市场级判断车道,内部**两段**——确定性 `market.factor`(因子 agent 算带参市场因子+走势)→ `market.regime`/`market.rotation`(LLM 读走势判断);产 regime 工件注入全局;判断走**经验库**(延迟标注 + 数值近邻类比检索 + 历史回放种子)。

**非目标**:① 具体数据源的抓取实现(本期只定接口,复用 `datafeed/live_client`);② 让 LLM 直接下单/动实盘信号(永远 advisory);③ 改动 `run_graph` 核心;④ 新前端页面(UI 只填充不重建)。

**红线(贯穿)**:达标产物一律 draft、采纳永远人审;每个承重数字可溯源否则 `[UNSOURCED]`;worker 编数/零工具调用/空产出 → `incomplete` 不计 completed;LLM 零买卖,唯确定性 clock 执行,影子不动真钱;**绝不静默回落到未配置 vendor**;PIT replay 对 > `as_of` 的行**物理拒绝**(`FutureDataRefused`,绝不触发回落);绝不自改代码/提示词;降级须标徽章、绝不冒充真。

---

## 1. 模块布局 `guanlan_v2/orchestration/`(新)

| 文件 | 单一职责 |
|---|---|
| `context.py` | `DataContext`(as_of/clock/mode/reader/run_preamble)+ `PitGuard`(replay 拒未来) |
| `spec.py` | `WorkerSpec` / `Plan` / `GateCfg` / `DebateCfg`(数据驱动的注册行) |
| `catalog.py` | worker 能力目录(Orchestrator 选子集的来源)+ skill 索引 |
| `dag.py` | 通用 LLM-worker DAG runner(topo 分层 · 层内并行 · `input_from` 上游注入 · 依赖门控 · 诚实分类) |
| `orchestrator.py` | 动态编排 LLM:goal + 目录 → 冻结 `Plan`;失败诚实终止或回落静态 preset(标 `source=preset_fallback`) |
| `optimize.py` | 通用 Evaluator-Optimizer(泛化 `research/loop`,`evaluate/gate/improve` 可插拔 + 停滞守卫) |
| `evaluator.py` | 四层 Evaluator(诚实闸→确定性指标→过拟合治理→归因反馈) |
| `governor.py` | 过拟合治理(窥视预算 / Deflated Sharpe / PBO / 复杂度罚 / walk-forward) |
| `honesty.py` | 诚实脊柱:`classify_worker`(incomplete 判定)、数字溯源校验、徽章 |
| `pool.py` | `ArtifactPool` 共享黑板(发布/订阅)+ `DebateState` |
| `schemas.py` | `Artifact` / `Provenance` / `NumberAnchor` / 结构化 payload(`ResearchPlan` 等) |
| `memory/` | `store.py`(CoALA 三层:语义/情景/程序)、`attribution.py`(确定性归因)、`experience.py`(`RegimeCase` 经验库 + 延迟标注 grader + 数值近邻检索) |
| `data/` | `symbols.py` / `errors.py` / `source.py` / `registry.py` / `reader.py`(接口层,获取暂缓) |
| `market/` | `factors.py`(市场因子计算,走 `run_graph`/PIT 口径) |
| `adapters/` | `luozi.py`(回测 replay)、`weiwo.py`(live/研究) |

**复用(不重造)**:`workflow/executor.run_graph`(worker/evaluator 的确定性计算工具)、`workflow/executor.topo_order`/`graph_signature`(dag.py 借用)、`autonomy/subagent.run_section_agent`(worker 执行原语:隔离 brief + 工具白名单 + seat 档位 + 预算 + 超时 + 文件交接 + confirm 自拒)、`research/store`(轮次落档)、`factorlib` draft、`console/curator`(记忆归档)、`console/tools.memory_write_impl`(keyed 记忆)、`seats/*`(落子回测/PA/`runBacktest`)、`datafeed/live_client`(数据源)、既有 `regime 因子族` / `市场温度护盾` / `industry lesson recall`。

---

## 2. 两大工作流

### 2.1 Orchestrator-Workers(动态分解)

`任务/目标 → Orchestrator LLM(动态拆解 → 冻结 Plan=worker DAG)→ 层内并行跑 worker → 发布/订阅共享池 → Synthesizer(sink worker)汇聚 → 候选`。

- Orchestrator 从 `catalog.py` 挑 worker 子集(不是每次全 21+ 个),按依赖连边,输出 `Plan` 并冻结成 artifact(可审计/可回放)。已知重复任务可跳过 Orchestrator 直接用静态 preset(便宜可复现,仿 Vibe)。
- `dag.run(plan, ctx, pool)`:`topo_order` 分层;层内 `parallel`(daemon 线程各自 `asyncio.run`,watcher 先例);每个 worker 的 brief = `system_prompt` + `## 上游产出`(按 `reads[]`/`input_from` 从池拉 summary)+ `ctx.run_preamble`(身份锚 + regime 工件 + past_context);经 `run_section_agent` 跑。
- **依赖门控**:上游非 `completed` → 下游 `blocked` 不派发(仿 Vibe,防"缺风控输入却出决策")。
- **结论 = sink worker 报告,无投票**;综合由那个末端 LLM 负责,引擎不算共识分。

### 2.2 Evaluator-Optimizer(迭代到指标)

`候选 → 确定性求值(run_graph/回测)→ 收益曲线+指标 → Evaluator 四层 → 达标?→ 是:draft 人审 / 否:结构化归因反馈 → Optimizer 改进 → 回环`。

```python
# optimize.py
def run_optimize(*, seed, ctx, max_rounds, governor,
                 evaluate: Callable[[Cand, DataContext], Metrics],   # 确定性
                 gate:     Callable[[Metrics], GateResult],
                 improve:  Callable[[Cand, Metrics, Feedback], Cand]) -> OptimizeResult
```

- 泛化 `research/loop.run_research_loop`:保留**停滞守卫**(候选签名比较,同签名=烧轮次→带警告重批一次仍同→诚实中断)、**诚实终止**(提案失败不降级模板)、**draft-only 落档**、每 run 一条 keyed 教训。
- `evaluate/gate/improve` 可插拔:落子注入 `runBacktest`(收益曲线),帷幄注入 `run_graph` 因子指标,Lane 0 注入经验库延迟标注。

### 2.3 影子组合(决定 1B)

Lane D 的 `dec.pm` 决策**不进实盘信号**,而是照记进一个虚拟账户 → **影子收益曲线**。确定性求值因此吐**两条曲线**:确定性策略(真实执行口径)+ 影子组合(LLM 决策口径)。后者专供 Evaluator-Optimizer 评估 LLM 决策质量,且不违"LLM 零买卖"红线。

---

## 3. Worker 目录(5 lanes + 跨切,24 个 spec / ~22 persona)

分档采用 FSI `reader / critic / writer`(仅 writer 能写/出决策)。来源标注:`帷幄`=已存在、`TA`=TradingAgents、`astock`=A股 fork、`FSI`=Anthropic。**worker 不能再 spawn 子 agent(FSI 硬约束,仅一层委派)→ Lane D 辩论由顶层 runner 编排。**

### 3.0 Lane 0 · 市场情境层(自上而下,先跑,条件化全局)

内部两段。产出 regime 工件被四处消费:① Orchestrator 调 worker 配比(risk-off 多派风控);② 注入每个下游 worker brief;③ 喂 PM/仓位(**接现有市场温度护盾,做其显式上游**);④ Evaluator 做 regime-aware 评估(错 regime 里赚钱打折)。

| id | 段 | 角色 | tools/data | 输出 | 来源 |
|---|---|---|---|---|---|
| `market.factor` | ①确定性 | 市场因子 agent:把原料算成带参市场因子+走势(见 §5),走 `run_graph`/PIT | `market_tape`·`fundflow`·`macro`·指数/广度 | `market_factor_report`(因子走势向量) | 帷幄(regime 因子族 + market_tape) |
| `market.regime` | ②LLM | 大盘走势判断:读因子走势(非原始标量)+ 经验库类比 → regime(牛/熊/震荡 × risk-on/off/overheat) | 读池 + `experience` | `regime_report`(结构化) | 帷幄 + 新 |
| `market.rotation` | ②LLM | 主线轮动判断:主线排序 + 阶段(启动/扩散/分化/退潮)+ 强度/持续性 | 读池 + 产业链框架 + `experience` | `rotation_report`(结构化) | 帷幄(fundflow/industry)+ 新 |

### 3.1 Lane A · 量化(5)

`quant.factor`(因子IC·帷幄 rescore/factor_ic)、`quant.model`(v4+DL集成·帷幄)、`quant.backtest`(vintage/OOS/PBO·帷幄 backtest cards)、`quant.fundamentals`(财报估值·**TA** + astock `get_profit_forecast`)、`quant.factor_miner`(小灶挖因子过 Sharpe/robust 门·帷幄 research/loop)。

### 3.2 Lane B · 量价几何(3,加深)

`pv.price_action`(15键确定性 PA + 可编辑方法论·帷幄 EV-017~026)、`pv.technical`(≤8 互补指标 + `get_verified_snapshot` 真值锚·**TA**)、`pv.microstructure`(五档/逐笔/炸板/主力·帷幄 live_book/market_tape/fundflow)。

### 3.3 Lane C · 文本(5)

`text.news`(快讯+全球·帷幄 kuaixun/news_marks~TA)、`text.sentiment`(**不调工具**、吃预取块、输出 band/score/confidence·**TA** 反捏造 #557/#796 + 帷幄 sentiment)、`text.research_report`(Kimi 研报抽取+旧报降权·帷幄)、`text.policy`(政策/窗口指导·**astock**)、`text.macro`(预测市场+打板温度·帷幄 macro + TA `get_prediction_markets`)。

### 3.4 Lane D · 决策/风控(6,有界辩论)

把现有线性报告链(bull/bear/辩护人/风控/写手)升级为**有界多轮辩论 + 轮次守卫**:`dec.bull`⇄`dec.bear`(≤2 轮,bear 晚一波逐条反驳)→ `dec.research_mgr`(裁多空 → 5 档评级 `ResearchPlan`·**TA**)→ `dec.risk_debate`(激进/稳健=风控/中性 三席,一个 spec 实例化 3×,`latest_speaker`+`count` 守卫,≤2 轮)→ `dec.pm`(A股约束终裁 `PortfolioDecision`·deep 档·注入 past_context)→ `dec.trader`(转 `TraderProposal`,**仅 advisory draft**)。

### 3.5 跨切(2)

`x.quality_gate`(数据质量 ABCDF·**astock**)、`x.number_critic`(**数字溯源门**:承重数字须溯源否则 `[UNSOURCED]`,拒捏造市值/价·帷幄 introspector + FSI 不可信输入隔离)。

### 3.6 skill 作者模型(FSI)

每个 skill 一个 `orchestration/skills/<name>/SKILL.md`:frontmatter 带 `name` + `description`(显式 "Perfect for / Not ideal for" 触发词),正文是开局 `## ⚠️ CRITICAL: Data Source Priority` 的清单式 playbook(= CoALA 程序记忆)。一份源 + `sync-skills.py` + `check.py` drift-lint 镜像进各 worker 包(单一事实源)。

---

## 4. 多源数据融合接口(接口先行,获取暂缓)

照搬 TA `dataflows/interface.py`:**双名间接**(agent 面方法名 ≠ vendor 适配名)+ `{method:{vendor:callable}}` 注册表 + config 即回落链 + **按类型异常路由**(异常种类数=路由反应数,加 vendor 零新 except),再补上 TA 没有、帷幄必须有的 **`DataContext` PIT 时钟**。

### 4.1 Symbol 归一(A股缝)

```python
@dataclass(frozen=True)
class Symbol:
    code: str          # 6 位无前后缀 "600519"
    exchange: str      # SH | SZ | BJ
    board: str         # main | star(688) | chinext(300) | bj(8/4)
    is_st: bool
    @property
    def dotted(self) -> str: ...      # "600519.SH"
    @property
    def limit_pct(self) -> float: ... # 0.05 ST / 0.20 star|chinext / 0.10 main

def normalize_symbol(raw: str) -> Symbol: ...
    # 纯语法(不联网):剥 .SH/.SZ/.BJ 与 SH/SZ/BJ → 6 位;首位定所;
    # 结果须匹配 ^[0-9]{6}$ 才可拼进缓存文件名(路径安全,astock 先例)
def resolve_name_to_code(raw: str, reader) -> Symbol: ...
    # 中文名/板块名 → 码;检测 CJK 查全市场名↔码表;拒行业/概念名(逼模型给 6 位码,绝不猜)
```

### 4.2 错误分类(路由的全部控制面)

```python
class DataError(Exception): ...
class NoDataError(DataError): ...       # 空 → 换下一个 vendor,记住
class StaleDataError(NoDataError): ...  # 最新行超 MAX_STALE_DAYS
class RateLimitError(DataError): ...    # 记日志 + 换下一个
class NotConfiguredError(DataError, ValueError): ...
class FutureDataRefused(DataError): ... # PIT 硬停,永不回落(前视=泄漏=bug)
```

### 4.3 DataSource + 注册表 + 路由(窄回落)

```python
class SourceRegistry:                                   # == TA VENDOR_METHODS
    def register(self, method, vendor, impl) -> None    # 加 vendor = 一行
    def resolve_chain(self, method, cfg) -> list[str]   # tool 级压过 category 级;逗号有序链;'default'=全部
    def dispatch(self, method, req, ctx) -> DataResult:
        # for vendor in resolve_chain(...):
        #   try: res=impl(req); ctx.pit_guard.check(res); return res
        #   except RateLimitError:    log; continue
        #   except NotConfiguredError: 记 first_error; continue     # 决定3:窄回落仅这两类触发跨 vendor
        #   except NoDataError as e:  记 last_no_data; continue
        #   except FutureDataRefused: raise                          # 永不回落
        # 终态:last_no_data → NO_DATA 哨兵('do not fabricate');
        #       OPTIONAL {'macro','prediction_markets','signal_data'} → DATA_UNAVAILABLE 哨兵(降级);
        #       core → raise first_error(broken primary 大声显形)
```

**决定 3 落地**:跨 vendor 回落**窄**(仅 `RateLimitError`/`NotConfiguredError`);同一 locale 的源冗余(mootdx→新浪、东财 try/except)塞进 **adapter 内部**;东财单会话限频 `_em_get`(`EM_MIN_INTERVAL≈1.0s`+jitter,反封),不限腾讯/新浪/同花顺/财联社/百度。

### 4.4 类别与注册(signal_data 独立可选)

```python
CATEGORIES = {
  "core_stock_apis": "a_stock",  "technical_indicators": "a_stock",
  "fundamental_data": "a_stock", "news_data": "a_stock",
  "signal_data": "a_stock",      # ★ A股独有,OPTIONAL:龙虎榜/北向/资金流/解禁/题材
  "macro_data": "polymarket_kalshi",  # OPTIONAL
  "prediction_markets": "polymarket", # OPTIONAL
}
# signal_data: get_dragon_tiger_board / get_northbound_flow(自缓 CSV) /
#              get_fund_flow / get_lockup_expiry / get_hot_stocks
```

adapter 层(非 agent 层)编码异质性:GBK 编码(腾讯/同花顺)、各源市场前缀规则、Referer/Origin 头、非交易日返回 `"N/A: 非交易日"`(不编数)。

### 4.5 DataReader facade

```python
class DataReader:                       # 绑定一个 DataContext,as_of/mode/clock 隐式
    def get_ohlcv(self, sym, start, end) -> str
    def get_indicators(self, sym, indicator, curr_date, look_back_days=30) -> str
    def get_verified_snapshot(self, sym, curr_date, look_back_days=30) -> str
    def get_fundamentals(self, ticker, curr_date) -> str
    def get_news(self, ticker, start, end) -> str
    def get_signal(self, method, sym, curr_date) -> str   # 龙虎榜/北向/…
    # 每个方法一行委托 registry.dispatch;返回带溯源头的字符串,空/陈旧 → NO_DATA 哨兵,绝不可脑补
```

### 4.6 DataContext + PitGuard(TA 没有、帷幄必须有)

```python
class DataMode(Enum):
    ONLINE="online"; PIT_REPLAY="pit_replay"; CACHED="cached"

class DataContext:
    as_of: str; clock: Clock; mode: DataMode; reader: DataReader
    run_preamble: str            # 身份锚 + regime 工件 + past_context,注入每 worker
    pit_guard: PitGuard

class PitGuard:
    # CACHED/soft: 丢 date > as_of 的行(按财报 fiscal-period-end 过滤)
    # PIT_REPLAY strict: 任一返回行 date > as_of → raise FutureDataRefused(泄漏=bug,永不回落)
    # 陈旧闸: 最新行 < as_of - MAX_STALE_DAYS → StaleDataError
    def check(self, result) -> DataResult: ...
```

`CACHED` 背端 = 帷幄 `G:\stocks\stock_data\pit_store` / `PitReader`;`news_coverage_floor` 与现有双闸(后端 `ts≤boundary`、前端揭示墙)接在 `PitGuard` 后。

---

## 5. 市场因子(Lane 0 `market.factor` 的计算参数)

LLM 不看原始标量;因子 agent 先把原料算成**带参市场因子+走势**(时间序列),LLM 读走势判断。全部走 `run_graph`/PIT 口径,可回测。

| 类 | 因子 | 计算 / 待设计参数 |
|---|---|---|
| 广度 | 涨跌家数比 AD | (涨−跌)/总;`MA5/MA20 + 斜率` |
| 广度 | 新高新低差 | NH−NL,窗口 `20/60日` |
| 广度 | 涨停强度/炸板率 | 涨停数 `3日EMA`;炸板率=炸板/(涨停+炸板) |
| 广度 | 连板高度/晋级率 | 最高连板 + 首板晋级率 |
| 广度 | **广度背离** ★ | `z(指数20日收益) − z(广度20日变化)`,`>阈值`=顶背离 |
| 资金 | 北向趋势 | `5/20日累计净额 + 斜率 + 250日分位` |
| 资金 | 主力净额分位 | 全市场主力净流入 `250日百分位` |
| 轮动 | 板块资金集中度 | `HHI` 或 top3 占比 |
| 轮动 | 主线扩散度 | 领涨题材内上涨个股广度 |
| 轮动 | 行业动量离散度 | 行业收益截面 `dispersion` |
| 波动/估值 | 已实现波动率 / 估值分位 | RV `20日`+短长比;指数 PE/PB `5年百分位` |
| 温度 | 打板温度 | 已有(market_tape) |

每因子参数 = 窗口/平滑(MA/EMA)/标准化(z-score/百分位/截面 rank)/背离阈值/频率。**参数不拍脑袋**:这些是市场择时因子,用经验库 `realized`(N 日后真实走向)当 ground truth 做 IC/命中率,甚至让 Evaluator-Optimizer 调参。闭环:因子 agent 算 → LLM 解读 → 写 case → 延迟标注 → 反过来验证/优化因子参数。

---

## 6. 记忆架构

### 6.1 短期 = 共享工件池 blackboard(`pool.py`)

run 级黑板,发布/订阅、不直接互调(MetaGPT 消息池 + Vibe 文件交接的合体)。下游按 `reads[]`/kind 订阅并自取(订阅过滤控上下文膨胀)。append-only、可审计、随 run 消亡或蒸馏进长期。

### 6.2 长期 = CoALA 三层(`memory/store.py`)

语义(事实/keyed 教训)、情景(过往 run/case)、程序(技能/配方/factorlib draft)。每 agent 一份可自编辑核心记忆(Letta 式)+ 一个 `[[链接]]` 互联库(A-MEM 式,`MEMORY.md` 已是)。按 role + 相关度(recency/importance/relevance)注入 worker brief。

### 6.3 反馈归因回路(`memory/attribution.py`,决定 3)

Evaluator 反馈 → **确定性归因(优先)**:① 边界门违反(哪件工件 metrics 先没过其 lane 门)② 引用链(Synthesizer/Evaluator 点名采纳却致败者)③ incomplete/失败节点。三条缩到 1–2 个 worker → **LLM 只在这 1–2 个里当裁判**。责任 worker Reflexion 自省 → 提炼教训(`scope=role+key`)→ **curator 把关**(去重/`[[链接]]`/不与既有事实冲突/不写编数/归档 语义/情景/程序)→ 才落长期。

### 6.4 经验库 RegimeCase(`memory/experience.py`,Lane 0 专用)

情景记忆的特化 + 类比检索,**延迟标注**闭环:

```python
class RegimeCase(BaseModel):
    as_of: str                    # 判断日,PIT 冻结
    features: dict[str, float]    # 因子 agent 加工后的因子向量(非原始标量)
    judgment: dict                # regime/主线/阶段/confidence/叙事
    realized: dict | None = None  # 延迟补:前向指数收益/主线超额/正确性分
    lesson: str | None = None     # Reflexion
    links: list[str] = []         # [[相似 case / 用到的 skill]]
```

生命周期:① 判断前**检索 k 个最像历史 case**(读其 `realized`+`lesson`,类比证据写进 prompt)→ ② worker 判断(RAG)→ ③ 落 `pending`(PIT 冻结)→ ④ **N 个交易日后确定性实测** `realized`(复用 rerank `matured` 门 / `basket_perf._closes` / vintage realized-date,**标签确定性算,不用 LLM**)→ ⑤ 归因+Reflexion → ⑥ curator → labeled 入库。

- **类比检索 = 数值近邻**(标准化特征向量的 cosine/距离),**无需 embedding**(帷幄知识库本无向量;市场态本身是数值向量,天然适配)。
- **冷启动 = 历史回放批量种子**:PIT 回放过去 N 年,features 与 realized 皆确定性可算,regime 标签由前向收益/回撤自动派生(或 LLM 打标 + 人审),一次性种起步库,不必等数月。

---

## 7. Evaluator 四层 + 过拟合治理

`evaluator.py` 四层(前三确定性,第四才用 LLM):
- **L0 诚实闸**:所有 worker `completed` + 候选无编数标记 → 否则拒评不打分。
- **L1 确定性指标**:`run_graph`/回测 → Sharpe·rank_ic·最大回撤·换手·胜率·尾部。
- **L2 过拟合治理**(`governor.py`):walk-forward/OOS robust + 按 `n_trials` 去偏的 Deflated Sharpe + 可选 PBO + 复杂度罚;**超窥视预算即停迭代**(防无限 p-hacking)。
- **L3 归因反馈**:LLM 读指标+曲线+各 lane 贡献 → 结构化反馈 `{改什么, 哪条 lane/worker 弱}`(**不打分**,分是 L1/L2 给的)——喂 Optimizer 与归因回路。

---

## 8. Schema(字段级,`spec.py` / `schemas.py` / `pool.py`)

```python
# 共享枚举(borrowed 自 TA schemas.py)
class PortfolioRating(str,Enum): BUY="Buy";OVERWEIGHT="Overweight";HOLD="Hold";UNDERWEIGHT="Underweight";SELL="Sell"
class TraderAction(str,Enum): BUY="Buy";HOLD="Hold";SELL="Sell"
class SentimentBand(str,Enum): BULLISH="Bullish";MILDLY_BULLISH="Mildly Bullish";NEUTRAL="Neutral";MIXED="Mixed";MILDLY_BEARISH="Mildly Bearish";BEARISH="Bearish"
class Tier(str,Enum): READER="reader";CRITIC="critic";WRITER="writer"
class Confidence(str,Enum): LOW="low";MEDIUM="medium";HIGH="high"

class WorkerSpec(BaseModel):
    id: str                     # "dec.pm",lane 前缀、稳定
    lane: Literal["market","quant","pv","text","decision","xcut"]
    persona: str
    system_prompt_ref: str      # SKILL 式 playbook 路径
    tier: Tier                  # reader | critic | writer
    skills: list[str]; tools: list[str]     # tools=[] 表示辩论/manager 节点
    reads: list[str]; writes: str
    output_schema: str | None = None
    model_tier: Literal["fast","reasoner","reasoner_deep"] = "reasoner"
    thinking_budget: int = 0
    round_role: str | None = None           # "bull"/"bear"/"aggressive"…
    mcp_servers: list[str] = []             # []=不可信输入隔离
    guardrails: list[str] = []              # ["no_signal_write","badge_on_degrade"]
    borrowed_from: Literal["帷幄","TA","astock","FSI","TA+astock"]

class Provenance(BaseModel):
    vendors: list[str]; tool_calls: list[str]; as_of: str; pit_mode: str
class NumberAnchor(BaseModel):              # 数字溯源门
    label: str; value: float; source_artifact_id: str | None = None   # None ⇒ [UNSOURCED]

class Artifact(BaseModel):
    id: str; kind: str; slot: str
    producer: str; run_id: str; seq: int    # 单调写序(辩论轮次)
    payload: dict[str,Any]                  # 结构化契约输出
    rendered_md: str                        # 串桥,始终填(散文/结构双表示)
    confidence: Confidence = Confidence.MEDIUM
    provenance: Provenance
    numbers: list[NumberAnchor] = []
    badges: list[str] = []                  # "延时源""回看""DL断供""非LLM"
    supersedes: str | None = None           # last-write-wins
    refuted_by: str | None = None           # critic 打回,留审计痕

# 结构化 payload(shape 借自 TA schemas.py)
class ResearchPlan(BaseModel): recommendation: PortfolioRating; rationale: str; strategic_actions: str
class TraderProposal(BaseModel): action: TraderAction; reasoning: str; entry_price: float|None=None; stop_loss: float|None=None; position_sizing: str|None=None
class PortfolioDecision(BaseModel): rating: PortfolioRating; executive_summary: str; investment_thesis: str; price_target: float|None=None; time_horizon: str|None=None
class SentimentReport(BaseModel): overall_band: SentimentBand; overall_score: float=Field(ge=0,le=10); confidence: Confidence; narrative: str
class RegimeReport(BaseModel): regime: str; risk_state: Literal["risk_on","risk_off","overheat"]; confidence: Confidence; drivers: list[str]; narrative: str
class RotationReport(BaseModel): mainlines: list[str]; stage: Literal["启动","扩散","分化","退潮"]; strength: float; persistence_days: int; narrative: str

class DebateState(BaseModel):               # TA InvestDebateState/RiskDebateState
    per_role_history: dict[str,str] = {}; history: str = ""
    latest_speaker: str = ""; current_response: str = ""; count: int = 0; judge_decision: str = ""

class ArtifactPool:                         # 命名槽黑板;写/读槽,非直接互调
    run_id: str
    def publish(self, art) -> None          # 设 seq、处理 supersedes
    def get(self, slot) -> Artifact | None
    def history(self, slot) -> list[Artifact]
    def subscribe(self, kinds) -> Subscription
    def debate(self, name) -> DebateState    # "invest" | "risk"
    def append_msg(self, msg) -> None        # 唯一 append-only 通道
    def snapshot(self) -> dict

class GateCfg(BaseModel): name: str; blocking: bool=True; threshold: float|None=None
class DebateCfg(BaseModel): name: str; seats: list[str]; max_rounds: int; judge: str
class Plan(BaseModel):
    id: str; run_id: str; goal: str; as_of: str; mode: str
    universe: list[str]; lane_order: list[str]; nodes: list[str]   # 拓扑序 WorkerSpec id
    debates: list[DebateCfg] = []; gates: list[GateCfg] = []
    run_preamble: str = ""; past_context: str = ""
    budget_tokens: int = 0; budget_seats: int = 24; stop_conditions: list[str] = []
```

---

## 9. 落子 & 帷幄 adapter(`adapters/`)

- **落子(replay)**:`ctx.mode=PIT_REPLAY`,`reader=PitReader@clock`;`evaluate=runBacktest`→收益曲线(确定性策略 + 影子组合);一次 `orchestrate` 出"按票决策链"(PA/因子/情绪 worker → Synthesizer=decide),**整段回测跑完再进 Evaluator-Optimizer**(非每 bar orchestrate,太贵)。
- **帷幄(live)**:`ctx.mode=ONLINE`,`reader=live_client@today`;`orchestrate` 开放研究任务;`evaluate=run_graph` 因子指标;产物 draft 入 factorlib。
- 两者共用同一 `dag.run`/`optimize.run_optimize`/`ArtifactPool`/记忆,仅换 `DataContext` 绑定。

---

## 10. 预算 / 档位 / 执行边界(贯穿)

诚实脊柱(draft-only · 数字溯源+徽章 · incomplete 闸)· 预算(座席≤24 · token 闸 · 仅 PM 深档 · 辩论≤2 轮)· 执行边界(唯确定性 clock 执行,影子不动真钱,LLM 零买卖)。

---

## 11. 测试与验收

- **单元**:`normalize_symbol` 纯语法边界(688/300/8/4/ST);`registry.dispatch` 窄回落 + 哨兵 + `FutureDataRefused` 不回落;`PitGuard` replay 拒未来行;`dag` 依赖门控(上游失败下游 blocked);`honesty.classify_worker`(空/编数/零工具→incomplete);`governor` 窥视预算硬闸;停滞守卫签名比较。
- **口径一致**:市场因子经 `run_graph` 与画布/帷幄逐位一致(镜像守护测试)。
- **e2e**:落子 replay 一段回测出双收益曲线;帷幄 live 一次开放研究出 draft;经验库历史回放种子 + N 日延迟标注补 `realized`;Lane D 有界辩论轮次守卫真拦。
- **红线回归**:LLM 不写实盘信号(影子专用);跨源绝不静默回落(注入未配置 vendor 被拒);draft 绝不自动上架。

---

## 12. 分期实施建议(供 writing-plans 细化)

1. **内核骨架**:`spec/pool/dag/honesty` + `run_section_agent` 适配 + `DataContext` 最小实现;跑通一个 3-worker 静态 Plan。
2. **数据接口**:`data/*`(接口 + 注册表 + PitGuard),获取先接 `datafeed/live_client` 占位。
3. **Orchestrator + Evaluator-Optimizer**:`orchestrator/optimize/evaluator/governor`,泛化 `research/loop`。
4. **Lane 0 + 市场因子 + 经验库**:`market/factors` + `memory/experience`(含历史回放种子 + 延迟标注 grader)。
5. **四车道 worker 目录 + skill 包**:填 `catalog` + `skills/`;Lane D 有界辩论。
6. **落子/帷幄 adapter + 影子组合**;红线回归全绿。

---

## 13. 未决 / 挂账

- **数据源具体获取实现**(决定 3 已定接口,vendor 抓取暂缓;akshare/tushare 取舍待定——astock 因积分墙弃 tushare、v0.2.18 声称零 akshare 依赖,本仓 `datafeed/live_client` 已用二者,以本仓为准)。
- **市场因子清单增删**(是否加涨停连板情绪、行业拥挤度、期权/融资情绪)。
- **typed 迁移次序**(现有 `bull/bear/辩护人/风控/写手` `reports/` 链分批迁进 `WorkerSpec` 的具体批次)。
- **Orchestrator 是否支持 human-in-the-loop 审计 Plan**(冻结后人审再执行)。
- **经验库 regime 标签体系**(自动派生阈值 vs LLM 打标 + 人审的比例)。
