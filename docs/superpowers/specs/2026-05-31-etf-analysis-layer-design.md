# ETF 深度分析 — 子项目 B:分析层 设计 (2026-05-31)

**Goal:** 对照个股 `stock-deep-dive`,给 financial-analyst 加一套 **ETF 深度分析多 agent 流水线**,产出单只 ETF 的研报(.md/.json/.html),`fa etf-report 510300` 一键生成。消费子项目 A 的 `ETFLoader`。

**Architecture:** **复用整条引擎脊柱**(Orchestrator 波次 DAG / DAGNode / SubAgent ABC / AgentMemory / load_preset / report-writer 写盘 / introspector)——这些资产无关。**新建 ETF 专属 agent 类**(个股 agent 的 SYSTEM_PROMPT 写死了个股概念,不能直接复用,但复用 SubAgent 基类 + 输出契约)。新 `config/swarm/etf-deep-dive.yaml` preset + ETF memory + `run_etf_report_oneshot` 驱动 + `fa etf-report` CLI。

**Tech Stack:** Python;fa `agent/`(SubAgent/Orchestrator/registry/memory)、`swarm/loader`、`tui.py`、`cli.py`;Pydantic schema;LLMClient;pytest(mock ETFLoader + LLM)。

**依赖:** 子项目 A(`ETFLoader` 7 方法 + `cn_data_etf` + `etf_*.parquet`),已在 worktree `etf-data-layer-wt` 完工。**B 建在同一分支之上。**

**参考(实现时读作模板):** 个股对应件——`agent/base.py`(SubAgent ABC)、`agent/tier2/fundamental_analyst.py`(分析师模板)、`agent/tier3/{bull_advocate,bear_advocate,risk_officer,report_writer,introspector}.py`、`agent/registry.py`、`agent/orchestrator.py`、`swarm/loader.py`、`config/swarm/stock-deep-dive.yaml`、`tui.py:run_report_oneshot`、`agent/memory.py`。

---

## 已锁定决策(用户 2026-05-31 确认)

1. **全量镜像**(~11 agent,对照个股 16-agent),非精简 MVP。
2. **v1 不做新闻 agent**(ETF 无个股 F10/龙虎榜;主题新闻价值有限)——靠 overseas-market-scanner + sector-rotation-analyzer 提供宏观+板块上下文。以后需要再加 etf-theme-news。
3. **复用引擎 + context agent,新建 ETF LLM/数据 agent 类**。
4. 在 worktree `etf-data-layer-wt`(A 之上)实现。

---

## DAG:`config/swarm/etf-deep-dive.yaml`

镜像 stock-deep-dive 的 4 tier。`variables`: `code`(required), `asof_date`(default today)。

| Tier | agent | deps | input_keys |
|---|---|---|---|
| 1 数据 | `etf-quote-fetcher` | [] | code, asof_date |
| 1 数据 | `etf-metrics-fetcher` | [] | code, asof_date |
| 1 上下文 | `overseas-market-scanner`(复用) | [] | asof_date |
| 1 上下文 | `sector-rotation-analyzer`(复用) | [overseas-market-scanner] | overseas-market-scanner |
| 2 分析 | `etf-holdings-analyst` | [etf-metrics-fetcher] | etf-metrics-fetcher, sector-rotation-analyzer |
| 2 分析 | `etf-technical-analyst` | [etf-quote-fetcher] | etf-quote-fetcher |
| 2 分析 | `etf-flow-analyst` | [etf-metrics-fetcher] | etf-metrics-fetcher |
| 2 分析 | `etf-valuation-analyst` | [etf-metrics-fetcher] | etf-metrics-fetcher |
| 3 辩论 | `etf-bull-advocate` | [4 analysts] | 4 analysts, etf-quote-fetcher |
| 3 辩论 | `etf-bear-advocate` | [4 analysts] | 4 analysts |
| 3 辩论 | `etf-risk-officer` | [bull, bear, etf-metrics-fetcher] | bull, bear, etf-metrics-fetcher |
| 3 写报告 | `etf-report-writer` | [all above] | quote, metrics, 4 analysts, bull, bear, risk |
| 4 | `etf-introspector` | [etf-report-writer + analysts] | (复用模式) |

---

## Agent 规格(新建,SubAgent 子类:NAME + Pydantic OUTPUT_SCHEMA + `_execute` + AgentMemory)

**T1 数据(纯 Python,无 LLM,调 ETFLoader):**
- `etf-quote-fetcher` → 输出 price/ret_5d/20d/60d/ma5/20/60/volatility/volume_ratio + meta(name/m_fee/c_fee/total_fee/benchmark/index_code/fund_type)。用 `fetch_etf_quote` + `fetch_etf_meta`。
- `etf-metrics-fetcher` → 一把梭:`fetch_etf_premium_discount` + `fetch_etf_nav`(末值+历史折溢价)+ `fetch_etf_flow`(申赎净额/AUM趋势)+ `fetch_tracking_error` + `fetch_etf_holdings`(top10+行业权重+HHI集中度)。输出结构化 dict。

**T2 分析师(LLM,各输出 `score: int [-2,2]` + `bull_points: list[str]` + `bear_points: list[str]` + 维度特有字段):**
- `etf-holdings-analyst`:holdings_score + top_holding_weight/sector_concentration_hhi/index_methodology_note。规则:持仓集中度过高/单票风险标注。
- `etf-technical-analyst`:technical_score + ma_state/rsi_state/breakout_signal(价格面,可参考个股 technical-analyst 模板)。
- `etf-flow-analyst`:flow_score + flow_regime(persistent_inflow/outflow/neutral)/aum_trend/liquidity_note。
- `etf-valuation-analyst`:valuation_score + premium_discount_state(premium/discount/fair)/tracking_error_level/fee_drag_note。**这是 ETF 特有"估值"轴**。

**T3:**
- `etf-bull-advocate`:thesis_bullets(每条 [V#] 锚:V1 主题顺风/V2 净流入动量/V3 折价/V4 方法论优势/V5 低费率/V6 流动性好…)+ target_price_high/base。≥2 条。
- `etf-bear-advocate`:thesis_bullets(每条 [F#] 锚:F1 主题拥挤已price-in/F2 跟踪漂移/F3 高费拖累/F4 持仓集中/F5 溢价回归/F6 AUM萎缩-清盘/F7 杠杆衰减)+ target_price_low/downside_pct。memory_mode=retrieval。
- `etf-risk-officer`(CRO,risk_score [-2,0] only):veto_flags + position_sizing_advice。**硬否决**:持续溢价>X% / 流动性过低(ADV/AUM) / 跟踪误差爆裂 / AUM 低于清盘线 / 杠杆反向长持。borrows_memory:[etf-bear-advocate]。
- `etf-report-writer`(唯一写盘):rating_overall = sum(持仓+技术+资金流+估值+风险)∈[-10,10];rating_dimensions{holdings,technical,flow,valuation,risk};action∈{buy,hold,sell,avoid,accumulate};target_price/stop_loss/position_pct[0,0.10];markdown_body(8段)+summary_json。复用个股 report-writer 的 pydantic+sanity-fix 守卫(veto→仓位0;rating≠sum→覆盖;action 一致性)。

**T4:** `etf-introspector`(复用模式,写 proposals 到 _pending_introspections,人审)。

---

## report-writer 8 段 ETF 模板(memory: `etf-report-writer/report_template.md`)
一、综合评级(5维表[持仓/技术/资金流/估值/风险] + 总评 + action + **上次回顾**)| 二、Variance(NAV/价/折溢价 vs 上次)| 三、持仓构成(top10/行业权重/集中度/跟踪指数)| 四、流动性与资金流(申赎净额/AUM趋势/换手)| 五、估值与跟踪(折溢价历史+实时/跟踪误差/费率)| 六、多空辩论(V锚/F锚)| 七、风控审查(否决/仓位/止损)| 八、操作建议(目标/仓位/止损/监控)

## ETF memory(`memories/<agent>/*.md`,热加载)
`etf_rating_system.md`(5 维 + 规则:AUM-tier 类比市值-tier,巨型 ETF 自下而上 alpha 归零)· `premium_discount_playbook.md` · `flow_regime_signals.md` · bull `v_anchors.md`(V1-Vn)· bear `f_anchors.md`(F1-Fn)· `_shared/`。

## CLI + 驱动
- `run_etf_report_oneshot(code, asof, out_dir, trace)`(镜像 `run_report_oneshot`:_ensure_registered → MemoryIndex → load_preset('etf-deep-dive') → Orchestrator.run({code,asof_date,out_dir}) → render_report)。
- `fa etf-report 510300 [--asof --out-dir --trace]`(cli.py 新 typer 命令,镜像 `report`)。
- 新 ETF agent 在 `tui.py:_ensure_registered` 注册。

---

## 测试
- 每个 ETF agent:单测 mock 上游输入(T2/T3 用 canned upstream dict + mock LLMClient 返回合规 JSON;T1 mock ETFLoader),验 OUTPUT_SCHEMA 校验 + 关键字段。
- preset 加载测试:load_preset('etf-deep-dive') 构出 DAGNode 列表、deps 正确。
- 端到端:mock ETFLoader + LLM 跑 Orchestrator,验 report-writer 出 .md/.json + rating=sum。
- 冒烟(真 LLM + 真 A 数据):`fa etf-report SH510300` 出报告(我亲跑,需 LLM key)。

## 非目标
- 新闻/主题 agent(v1 跳过)。
- 个股概念移植(游资/涨停/筹码/F10/stock model-predictor)。
- B 不改 A 的数据层;不改个股 stock-deep-dive。

## 风险
| 风险 | 缓解 |
|---|---|
| ETF agent prompt 质量(锚系统/规则)| 先借个股 prompt 结构,ETF 概念填充;memory markdown 可热改迭代 |
| LLMClient.for_agent 需要 per-agent 配置 | 实现时读个股 agent 怎么调 LLMClient,照搬 |
| 5 维评级语义(AUM-tier 类比)| etf_rating_system.md 写清;report-writer sanity-fix 复用 |
| context agent(overseas/sector)对 ETF 的适配 | 直接复用其输出作宏观/板块上下文,holdings-analyst 消费 sector-rotation |
| 真 LLM 冒烟需 key | 单测全程 mock LLM;冒烟我用配好的 key 亲跑 |
