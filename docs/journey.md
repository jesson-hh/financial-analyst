# financial-analyst — Build Journey & Architecture Overview

<div align="center">

**🌐 Language**:
[🇬🇧 English](#english) ｜ [🇨🇳 中文](#-中文)

</div>

---

<a id="english"></a>

## 🇬🇧 English

> *Jump to: [中文](#-中文)*

A two-week build log of `financial-analyst`, an A-share single-stock
deep-dive multi-agent research workstation. From empty repo to v1.4.2
on PyPI with 440 quantitative alphas and 21 sub-agents.

### 1. What it is, in one paragraph

`financial-analyst` is a CLI + TUI workstation for researching Chinese
A-share equities. You point it at a stock code; it orchestrates ~21
sub-agents in three trust tiers (data fetchers → analysts → decision
makers) to produce a deep-dive report with star rating, target price,
stop-loss, position size, and supporting bull/bear arguments. Every
sub-agent has its own pluggable markdown memory; every untrusted source
(news, social media, F10) is parsed through pydantic-validated JSON
before it can influence a decision. The quant layer ships **440 named
alphas** across three reference catalogues (WorldQuant 101, GTJA 191,
Qlib Alpha158) with `IC / IR / hit-rate` benchmarking, dynamic top-N
selection, and direct integration into the LLM analyst prompts.

```bash
pip install financial-analyst
financial-analyst report SH600519
```

### 2. Architecture — three trust tiers + plug-in everything

#### Trust isolation

```
┌─ Tier 1: data fetchers (READ-ONLY filesystem, mock-able loaders) ─┐
│                                                                    │
│  quote-fetcher  factor-computer  model-predictor  news-reader      │
│  f10-reader     model-zoo-snapshot                                 │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
                              ↓ JSON
┌─ Tier 2: analysts (parse + interpret data only) ──────────────────┐
│                                                                    │
│  fundamental-analyst   technical-analyst   whale-analyst           │
│  quant-analyst         mainline-classifier                         │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
                              ↓ JSON
┌─ Tier 3: decision makers (only report-writer has write tool) ─────┐
│                                                                    │
│  bull-advocate   bear-advocate   risk-officer   report-writer      │
│  market-scanner  morning-brief-writer   intraday-reviewer          │
│  introspector (dream-loop)   ask-agent (NL router)                 │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

Only `report-writer` has the `write` filesystem tool. Tier 1 + Tier 2
output pydantic-validated JSON. Any untrusted text (news content,
social posts, F10 announcements) is constrained to `news-reader` and
`f10-reader` and emerges as schema-locked structured data. A
malicious news source can never leak instructions into a downstream
analyst's prompt because the structure strips free-form text.

#### Pluggable everywhere

- **Sub-agents**: drop a `.py` file under `config/plugins.yaml` to register a new agent. Discovered at CLI startup.
- **LLM providers**: LiteLLM under the hood; configure `config/llm.yaml` to switch between Anthropic / OpenAI / Qwen / DeepSeek / Ollama (and per-agent overrides, so e.g. `whale-analyst` can use Qwen-Plus while `report-writer` uses Sonnet).
- **Data loaders**: `BaseLoader` ABC. Ship `TushareLoader` (raw HTTP) and `QlibBinaryLoader`. Drop in CSV / Parquet / your own.
- **Models**: `ModelRegistry` for ML predictors. Plug in LGB, FM cluster, TSFM models.
- **Memories**: per-agent markdown under `memories/<agent>/*.md`. Edit a file → next agent invocation picks it up. SQLite FTS5 full-text retrieval; falls back to `load_all()` when search returns thin results.
- **Alphas**: register via `AlphaSpec` decorator in `factors/zoo/{family}/alphas.py`. The 440-alpha zoo is just the curated default.

### 3. Build journey — chronological

#### Phase 0: Design & scaffolding (Day −2 to 0)

Studied two open-source agent frameworks:

- **HKUDS/Vibe-Trading**: A multi-market quant agent with 29 swarm presets, 452 formulaic alphas, and a `alpha bench` CLI. We borrowed the zoo architecture and CLI shape.
- **anthropics/financial-services**: 3-tier trust isolation and schema-locked sub-agent JSON. We borrowed the trust model and pydantic discipline.

What we kept vs. what we changed: kept the trust tiers, kept the zoo registry pattern, dropped the 29-preset shotgun (our 4 are deeper), dropped the live-trading bias (we're a research tool, not a bot), added the Chinese A-share specificity (Tushare loader, 申万 industry classifier, xueqiu sentiment, 14 R7-R20 sentiment signals from the user's 5-year G:\stocks research).

#### Phase 1: Foundation (v0.1.0 – v0.6.0)

- **v0.1-0.4**: CLI + TUI shell, 13 sub-agents, YAML swarm presets, pluggable memory, FTS5 retrieval, dream loop, MCP server, ask-agent.
- **v0.5**: natural-language router (`ask` command, 6 tools).
- **v0.6**: pypi publish. First public release.

#### Phase 2: Data integrations (v1.0.0 – v1.2.2)

- **v1.0**: Docker + README polish + RELEASE_NOTES.
- **v1.1**: OpenCLI integration → local NewsDB. 4 collectors (eastmoney 7×24 / 龙虎榜 / 十大流通股东 / sinafinance 7×24) into SQLite with FTS5.
- **v1.2.0**: xueqiu cookie-mode collectors (3 more: comments / hot stocks / earnings dates). Whale-analyst now reads `social_posts` for retail sentiment.
- **v1.2.1** (HOTFIX): caught Windows cmd.exe transcoding bug — every Chinese character in the NewsDB was silently `���` mojibake because `subprocess` with `shell=True` ran node's utf-8 stdout through the GBK console code page. Fix: parse the npm `.CMD` shim, call `node <main.js>` directly with `shell=False`.
- **v1.2.2** (HOTFIX): two more bugs landed together:
  1. `social_posts` dedup collapse — xueqiu's `{author, text, url}` items had no `id` field; the upsert's `INSERT OR REPLACE` key collapsed all 30 comments to one row.
  2. `whale-analyst` schema drift — SYSTEM_PROMPT listed the policy but not the JSON schema; LLM hallucinated its own keys, pydantic silently dropped them, retail-sentiment insights never reached `report-writer`.

#### Phase 3: Alpha Zoo (v1.3.0 – v1.4.2) — the 2-day push

This is where the project changed scale. We started v1.3.0 with **22 hand-picked alphas** and ended v1.4.2 with **440 alphas across three reference catalogues** — a 20× expansion in two days.

| Version | Headline | Alpha count | Days |
|---------|----------|------------:|:----:|
| v1.3.0  | Zoo skeleton + bench CLI + sample30 universe | 22 | D1 |
| v1.3.1  | +27 ports across both families | 49 | D1 |
| v1.3.2  | qlib158 family seeded | 104 | D1 |
| v1.3.3  | **regbeta / regresi / rsqr operators** unlock regression-based alphas | 142 | D1 |
| v1.3.4  | **`alpha snapshot` → `factor-computer` → `quant-analyst`**: zoo signals reach reports | 142 | D2 |
| v1.3.5  | +148 alpha mass-port | 290 | D2 |
| v1.3.6  | +74 alphas, near completion of two catalogues | 364 | D2 |
| v1.4.0  | **IndustryLoader** + 19 IndNeutralize alpha101 | 383 | D2 |
| v1.4.1  | catalogue completion: alpha101 100%, gtja191 99%, qlib158 95% | 440 | D2 |
| v1.4.2  | **dynamic top-N selection** + sign-agnostic LLM prompt | 440 | D2 |

##### Key zoo decisions

**Architecture choices**:
- `compute(panel) → pd.Series` stateless API for every alpha. No cross-call state. Trivially parallelisable later.
- Per-code groupby for every time-series operator (`ts_max`, `delta`, `correlation`, etc.) so windows never bleed across stocks.
- `min_periods=window` everywhere → alphas never emit partial-window signals.
- `MultiIndex(datetime, code)` panel. Cross-sectional ops (`rank`, `scale`, `indneutralize`) group by `datetime`; time-series ops group by `code`.

**Operator catalogue**:
- 30+ operators total: `rank`, `ts_sum/mean/max/min`, `ts_rank`, `ts_argmax/argmin`, `delta`, `delay`, `correlation`, `covariance`, `decay_linear`, `wma`, `sma` (GTJA-style EWMA), `signedpower`, `scale`, `indneutralize`, plus regression triplet `regbeta / regresi / rsqr` and `sequence` (synthetic time index).

**Three families ported**:
- **alpha101**: WorldQuant 101 Formulaic Alphas (Kakushadze 2015, arXiv:1601.00991). 101/101 = **100%**.
- **gtja191**: Guotai Junan Securities 191 Alphas (国泰君安 2017). 189/191 = **99%**. The 2 unportable are `gtja143` (recursive `SELF`) and `gtja149` (benchmark-index beta).
- **qlib158**: Microsoft Qlib Alpha158. 150 features — many window variants of the same underlying feature, so 95% practical coverage.

**Bench / IC analysis**:
- `alpha bench --universe X --since Y --until Z --fwd-days 5` computes cross-sectional rank-IC of each alpha against forward 5-day returns.
- Sorted by `|rank_IR|` descending; output includes `ic`, `rank_ic`, `ir`, `rank_ir`, `hit_rate`, `n_dates`, `n_obs`.
- 440-alpha bench on 868 CSI300 codes × 144 days runs in **2m 43s**.

**Key empirical findings from real CSI300 bench (2024-H2)**:
- `qlib158` family dominates: **30% of qlib158 alphas have |rank_IR| > 0.25** vs 20% for gtja191 vs 12% for alpha101.
- **Volatility-based features rule** on CSI300 2024-H2: `qlib_VSTD60 (+0.54)`, `qlib_STD10 (-0.42)`, `gtja095 (-0.43)`, `qlib_KLEN (-0.35)` — five of top eight.
- **Sample30 (30 stocks) overfits badly**: sample30 leaders lost 44-100% of their signal magnitude on csi300. Rule of thumb: trust nothing tested on <100 stocks.

##### Integration story

- **v1.3.4**: built `alpha snapshot` CLI → cached parquet → factor-computer lookup → quant-analyst sees `zoo_signals` block. Initially used a hardcoded `PRODUCTION_TOP10` curated list (`qlib_VSTD60, gtja095, qlib_STD10, ...`) with a fixed sign convention table in SYSTEM_PROMPT.
- **v1.4.2**: replaced the hardcoded list with **dynamic top-N selection** from the latest cached bench. Snapshot rows now carry `bench_rank_ic / bench_hit_rate / bench_n_dates` so the LLM can interpret each alpha's direction from its bench-validated sign, not from a hardcoded prompt section.
  - Workflow: weekly cron runs `alpha bench --save` then `alpha snapshot auto --top-n 20`.
  - Reports auto-pick up the new top-20.
  - LLM verified output (SH600519, 2024-12-31): "qlib_WVMA60 rank_pct=91.1% with bench_rank_ic=+0.052 (positive-class) → bullish reading from this alpha."

#### Phase 4 (future): unblockers for the last 2 alphas

- **gtja143**: recursive `SELF` reference. Needs an optional `compute_iterative(panel, state) → (series, state)` API. Planned for v1.5.x.
- **gtja149**: benchmark-relative beta. Needs `BenchmarkLoader` to carry CSI300 close as a parallel series. Planned for v1.5.x.

### 4. Numbers at a glance

| Metric | v0.1.0 | v1.4.2 |
|--------|-------:|-------:|
| Sub-agents | 13 | 21 |
| CLI commands | ~14 | ~25 |
| Alphas | 0 | **440** |
| Alpha families | 0 | 3 |
| Operators | 0 | 30+ |
| Memory files | 25 | 50+ |
| Tests | ~180 | 350+ |
| Lines of code | ~10k | ~22k |
| PyPI releases | 1 | 12 |
| Build days | 14 | 14 (final 2 = zoo) |

### 5. Lessons learned

#### What worked

**Schema-first sub-agent design.** Pydantic-validated JSON outputs eliminate prompt-injection risk and make the orchestrator deterministic. The schema-drift bug in `whale-analyst` (v1.2.2) was caught only because pydantic enforced fall-through to defaults — without that, the silent insight loss would have lasted weeks.

**Per-agent memory as markdown.** Editing a `.md` file and seeing the next report pick up the new rule, without redeploying, is the single biggest contributor to fast iteration. The 25 → 50+ memory files represent the user's 5-year G:\stocks research distilled into prompt context — and changing them is `git diff` not engineering.

**Bench-driven alpha selection.** Hardcoding `PRODUCTION_TOP10` worked for a week. Replacing it with dynamic top-N from a rolling bench (v1.4.2) means the system tracks regime shifts without manual prompt edits. The cost is one weekly cron; the benefit is no prompt rot.

**Tushare raw HTTP > tushare library.** The official package's round-robin DNS hung intermittently on Windows. A 5-line `requests.post` to `http://api.tushare.pro` ran flawlessly. Sometimes the simplest client is the right client.

#### What hurt

**Sample30 overfitting**. The first zoo bench was on 30 hand-picked stocks because it ran in seconds. Top signals like `qlib_CNTN60` had `rank_IR = -0.605` on sample30, then collapsed to `-0.100` on CSI300 — an 80% degradation. We caught it before relying on the results in prod, but rule-of-thumb learned: **trust nothing tested on <100 stocks**.

**Windows encoding everywhere**.
- cmd.exe transcoding utf-8 → GBK in `subprocess` shell=True (v1.2.1).
- Twine + Rich progress bar crashing on `•` in GBK console (v0.6.0).
- pandas FutureWarning on `pct_change(fill_method=)` polluting bench output (v1.3.5).
- Each took ~30 minutes to root-cause and fix. **Windows is a first-class environment**; assume nothing is utf-8.

**Silent operator failures**. Three times in the zoo build, an alpha was registered but its compute function failed at runtime because an operator wasn't imported (`product`, `log`, `indneutralize`). Pydantic caught nothing because the alpha was never called until bench time. The `compute_error` status in bench output was added specifically to make these visible — but better: we now lint imports against the operator catalogue. (Planned cleanup, v1.5.x.)

#### What was over-engineered (in hindsight)

- The dream loop (`OutcomeTracker → Introspector`) is conceptually elegant but underused at current scale. Will become valuable when there's enough report history to detect patterns.
- 29-preset swarm shotgun (Vibe-Trading's approach). We have 4 deep presets and they're enough. Preset count is vanity; preset depth is reach.

### 6. Where to next

In rough priority order, based on user value × cost:

1. **Pre-ST regulatory filter** (S, A-share-specific): four-rule screener for ST candidates (consecutive losses, audit opinion, net assets, dividend gap). Wires into `bear-advocate` and `morning-brief-writer`. Likely v1.5.
2. **TDX formula export** (S, last-mile retail): top-picks → 通达信 selection-formula string the user can paste into their broker. Closes the research-to-execution gap.
3. **2025-Q1 out-of-sample bench**: rerun the 440-alpha zoo on a fresh quarter to confirm the volatility-theme finding holds.
4. **`BenchmarkLoader` + iterative compute** (M): unlocks `gtja143` and `gtja149`, closes the catalogue to 100%.
5. **Shadow-account analysis**: parse 同花顺 / 东方财富 trade exports, KMeans-cluster round-trips into implicit rules, replay as shadow backtest. Novel feature; needs user trade history to validate.

### 7. Quick reference for new contributors

```bash
# Setup
pip install financial-analyst
financial-analyst doctor                     # env sanity check

# Data
financial-analyst industry refresh           # one-time, Tushare → industry cache
financial-analyst news-collect --sources kuaixun,longhu --limit 200

# Alphas
financial-analyst alpha list                 # all 440 registered
financial-analyst alpha show alpha089        # formula + paper + description
financial-analyst alpha bench --universe csi300_active \
    --since 2024-06-01 --until 2024-12-31 --save
financial-analyst alpha snapshot auto --universe csi300_active \
    --until 2024-12-31 --top-n 20

# Reports
financial-analyst report SH600519 --asof 2024-12-31
financial-analyst ask "为什么茅台技术面这么弱"
financial-analyst morning-brief
financial-analyst mainline-classify
```

Memories live under `memories/<agent>/*.md`. Sub-agents are `src/financial_analyst/agent/tier{1,2,3}/`. Alphas are `src/financial_analyst/factors/zoo/{alpha101,gtja191,qlib158}/`.

**Contribute by editing markdown. The infrastructure is done.**

<div align="right">

[⬆ Back to top](#financial-analyst--build-journey--architecture-overview) ｜ [Switch to 中文](#-中文)

</div>

---

<a id="-中文"></a>

## 🇨🇳 中文

> *跳转: [English](#english)*

`financial-analyst` 的两周建设复盘. 从空仓库到 PyPI v1.4.2, 内含 **440 个量化因子** + **21 个 sub-agent**.

### 1. 一段话讲清是什么

`financial-analyst` 是一个 A 股个股深度研究的 CLI + TUI 工作站. 给它一个股票代码,
它会调度 **~21 个 sub-agent** 分三个信任层 (数据拉取 → 分析师 → 决策) 产出一份
研报: 星级评级 / 目标价 / 止损 / 仓位 / 看多看空辩论 + 风控审查. 每个 sub-agent
有独立的 markdown memory; 每个不可信源 (新闻 / 社交媒体 / F10) 都通过 pydantic
强制 JSON 化后才能影响决策. 量化层带 **440 个名命因子**, 覆盖三个权威家族
(WorldQuant 101 / 国泰君安 191 / Qlib Alpha158), 完整 IC/IR/hit_rate bench +
动态 top-N 选择 + 直接接入 LLM 分析师 prompt.

```bash
pip install financial-analyst
financial-analyst report SH600519
```

### 2. 架构 — 三层信任 + 处处可插拔

#### 信任隔离

```
┌─ Tier 1: 数据拉取 (只读文件系统, mock 友好) ────────────────────────┐
│                                                                    │
│  quote-fetcher  factor-computer  model-predictor  news-reader      │
│  f10-reader     model-zoo-snapshot                                 │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
                              ↓ pydantic JSON
┌─ Tier 2: 分析师 (只读 + 解读) ─────────────────────────────────────┐
│                                                                    │
│  fundamental-analyst   technical-analyst   whale-analyst           │
│  quant-analyst         mainline-classifier                         │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
                              ↓ pydantic JSON
┌─ Tier 3: 决策 (只 report-writer 有 write 文件权限) ───────────────┐
│                                                                    │
│  bull-advocate   bear-advocate   risk-officer   report-writer      │
│  market-scanner  morning-brief-writer   intraday-reviewer          │
│  introspector (dream-loop)   ask-agent (NL 路由)                  │
│                                                                    │
└────────────────────────────────────────────────────────────────────┘
```

只有 `report-writer` 能写文件. Tier 1 和 Tier 2 全部输出 pydantic 校验过的 JSON.
任何不可信文本 (新闻正文 / 社交帖 / F10 公告) 只能被 `news-reader` 和 `f10-reader`
读取, 出来时已经是 schema-locked 结构化数据. 恶意新闻源没法把 prompt 注入下游
分析师 — 因为结构化处理把自由文本剥光了.

#### 处处可插拔

- **Sub-agent**: 在 `config/plugins.yaml` 里挂个 `.py` 文件就注册一个新 agent. CLI 启动时自动发现.
- **LLM provider**: LiteLLM 抽象层. `config/llm.yaml` 切换 Anthropic / OpenAI / Qwen / DeepSeek / Ollama, 支持 per-agent override (例如 `whale-analyst` 用 Qwen-Plus, `report-writer` 用 Sonnet).
- **数据 loader**: `BaseLoader` 抽象基类. 内置 `TushareLoader` (原始 HTTP) + `QlibBinaryLoader`. 接 CSV / Parquet / 自定义都行.
- **模型**: `ModelRegistry`, 插 LGB / FM cluster / TSFM.
- **Memory**: 每个 agent 在 `memories/<agent>/*.md` 下放 markdown. 改完文件下次调用就生效. SQLite FTS5 全文检索, 没命中时 fallback 到 `load_all()`.
- **因子**: 用 `@register(AlphaSpec(...))` 装饰器注册到 `factors/zoo/{family}/alphas.py`. 440 个因子就是默认包.

### 3. 建设历程 — 按时间线

#### Phase 0: 设计 + 搭架子 (Day −2 至 0)

研究了两个开源 agent 框架:

- **HKUDS/Vibe-Trading**: 多市场量化 agent, 29 个 swarm preset, 452 个 alpha 公式, 自带 `alpha bench` CLI. 我们借鉴了 zoo 架构和 CLI shape.
- **anthropics/financial-services**: 三层信任隔离 + schema-locked sub-agent JSON. 我们借鉴了信任模型和 pydantic 纪律.

**保留 vs 改造**: 保留三信任层, 保留 zoo 注册模式, 砍掉 29-preset 散弹 (我们的 4 个更深), 砍掉实盘交易导向 (我们是研究工具), 加上 A 股专属性 (Tushare loader / 申万行业 / 雪球情绪 / 用户 5 年 G:\stocks 研究里的 R7-R20 共 14 个 S/SS 级情绪信号).

#### Phase 1: 地基 (v0.1.0 – v0.6.0)

- **v0.1-0.4**: CLI + TUI 外壳, 13 个 sub-agent, YAML swarm preset, 可插拔 memory, FTS5 检索, dream loop, MCP server, ask-agent.
- **v0.5**: 自然语言路由器 (`ask` 命令, 6 个工具).
- **v0.6**: 首次发 PyPI.

#### Phase 2: 数据集成 (v1.0.0 – v1.2.2)

- **v1.0**: Docker + README 抛光 + RELEASE_NOTES.
- **v1.1**: OpenCLI 集成 → 本地 NewsDB. 4 个采集器 (东方财富 7×24 / 龙虎榜 / 十大流通股东 / 新浪 7×24) 进 SQLite + FTS5.
- **v1.2.0**: 雪球 cookie-mode 采集器 (3 个: 个股评论 / 热股榜 / 财报日历). whale-analyst 现在能读 `social_posts` 当散户情绪.
- **v1.2.1** (HOTFIX): 抓出 Windows cmd.exe 编码转换 bug — NewsDB 里所有中文字符都静默变成了 `���` 乱码, 因为 `subprocess` 加 `shell=True` 让 cmd.exe 把 node 的 utf-8 stdout 按 GBK 控制台码页转码. 修法: 解析 npm 的 `.CMD` shim 找到底层 `main.js`, 直接 `node <main.js>` 调用, `shell=False`.
- **v1.2.2** (HOTFIX): 同批修两个 bug:
  1. `social_posts` 萃合塌缩 — 雪球 `{author, text, url}` 没 `id` 字段, upsert 的 `INSERT OR REPLACE` 主键把所有 30 条评论压成 1 行.
  2. `whale-analyst` schema 漂移 — SYSTEM_PROMPT 写了 policy 但没列 JSON schema, LLM 自己造字段, pydantic 默默用默认值填空, 散户情绪洞察根本没传到 `report-writer`.

#### Phase 3: Alpha Zoo (v1.3.0 – v1.4.2) — 2 天大冲刺

这是项目规模换挡的节点. v1.3.0 起步只有 **22 个手挑因子**, v1.4.2 收尾时是 **440 个因子覆盖三大权威家族** — 两天内 20× 扩张.

| 版本 | 关键产出 | 因子数 | 天 |
|---|---|---:|:-:|
| v1.3.0 | zoo 骨架 + bench CLI + sample30 universe | 22 | D1 |
| v1.3.1 | +27 移植 | 49 | D1 |
| v1.3.2 | qlib158 家族落地 | 104 | D1 |
| v1.3.3 | **regbeta / regresi / rsqr operator** 解锁回归类 | 142 | D1 |
| v1.3.4 | **`alpha snapshot` → `factor-computer` → `quant-analyst`**: zoo 信号入研报 | 142 | D2 |
| v1.3.5 | +148 因子批量移植 | 290 | D2 |
| v1.3.6 | +74 因子, 两大家族近完工 | 364 | D2 |
| v1.4.0 | **IndustryLoader** + 19 个 IndNeutralize alpha101 | 383 | D2 |
| v1.4.1 | catalogue completion: alpha101 100%, gtja191 99%, qlib158 95% | 440 | D2 |
| v1.4.2 | **动态 top-N 选择** + sign-agnostic LLM prompt | 440 | D2 |

##### Zoo 关键决策

**架构选择**:
- 每个因子是 `compute(panel) → pd.Series` 的无状态 API. 没有跨调用状态. 方便后续并行化.
- 每个时间序列 operator (`ts_max` / `delta` / `correlation` 等) 都按 `code` groupby, 窗口绝不跨股流动.
- 处处 `min_periods=window` → 因子绝不产出部分窗口信号.
- `MultiIndex(datetime, code)` 面板. 横截面 op (`rank` / `scale` / `indneutralize`) 按 `datetime` groupby; 时序 op 按 `code` groupby.

**Operator 目录**:
30+ operator: `rank`, `ts_sum/mean/max/min`, `ts_rank`, `ts_argmax/argmin`, `delta`, `delay`, `correlation`, `covariance`, `decay_linear`, `wma`, `sma` (GTJA 风格 EWMA), `signedpower`, `scale`, `indneutralize`, 加回归三件套 `regbeta / regresi / rsqr` 和 `sequence` (合成时间索引).

**三个家族**:
- **alpha101**: WorldQuant 101 Formulaic Alphas (Kakushadze 2015, arXiv:1601.00991). **101/101 = 100%**.
- **gtja191**: 国泰君安 191 短周期价量阿尔法因子 (2017). **189/191 = 99%**. 不可移植的 2 个: `gtja143` (递归 `SELF`) 和 `gtja149` (基准指数 beta).
- **qlib158**: 微软 Qlib Alpha158. 150 个特征 — 很多是同一底层特征的窗口变体, 实用覆盖率 **95%**.

**Bench / IC 分析**:
- `alpha bench --universe X --since Y --until Z --fwd-days 5` 算每个因子的横截面 rank-IC vs 未来 5 日收益.
- 按 `|rank_IR|` 倒序, 输出包含 `ic / rank_ic / ir / rank_ir / hit_rate / n_dates / n_obs`.
- **440 因子 × 868 CSI300 股 × 144 天 bench 在 2 分 43 秒跑完**.

**真实 CSI300 (2024-H2) 实证发现**:
- `qlib158` 家族屠榜: **30% 的 qlib158 因子有 |rank_IR| > 0.25**, 远超 gtja191 (20%) 和 alpha101 (12%).
- **波动率主题主导** CSI300 2024-H2: `qlib_VSTD60 (+0.54)` / `qlib_STD10 (-0.42)` / `gtja095 (-0.43)` / `qlib_KLEN (-0.35)` — top 8 里 5 个是波动率.
- **sample30 (30 股) 严重 overfit**: sample30 的 top 7 因子上 csi300 后 **rank_IR 缩水 44-100%**. 教训: **<100 股的 bench 结果不可信**.

##### 集成故事

- **v1.3.4**: 建好 `alpha snapshot` CLI → 缓存 parquet → factor-computer 查表 → quant-analyst 看到 `zoo_signals` 块. 初版用硬编码 `PRODUCTION_TOP10` 清单 (`qlib_VSTD60, gtja095, qlib_STD10, ...`), SYSTEM_PROMPT 里写死 sign convention.
- **v1.4.2**: 把硬编码清单换成**动态 top-N 选择**, 从最新缓存 bench 里挑. snapshot 行加 `bench_rank_ic / bench_hit_rate / bench_n_dates` 元数据, LLM 从每行的 `bench_rank_ic` 符号自己推方向, 不再依赖硬编码的 prompt 段.
  - 工作流: 每周 cron 跑 `alpha bench --save`, 然后 `alpha snapshot auto --top-n 20`.
  - 报告自动用新 top-20.
  - LLM 实测输出 (SH600519, 2024-12-31): "qlib_WVMA60 rank_pct=91.1% with bench_rank_ic=+0.052 (positive-class) → bullish reading from this alpha."

#### Phase 4 (未来): 解锁最后 2 个 alpha

- **gtja143**: 递归 `SELF` 引用自己上一步输出. 需要新增可选的 `compute_iterative(panel, state) → (series, state)` API. 计划 v1.5.x.
- **gtja149**: 基准指数相对 beta. 需要 `BenchmarkLoader` 把 CSI300 收盘当作并行 series 接入 panel. 计划 v1.5.x.

### 4. 关键数字对比

| 指标 | v0.1.0 | v1.4.2 |
|---|---:|---:|
| Sub-agent 数 | 13 | 21 |
| CLI 命令数 | ~14 | ~25 |
| 因子数 | 0 | **440** |
| 因子家族 | 0 | 3 |
| Operator 数 | 0 | 30+ |
| Memory 文件 | 25 | 50+ |
| 测试数 | ~180 | 350+ |
| 代码行 | ~10k | ~22k |
| PyPI 发布 | 1 | 12 |
| 建设天数 | 14 | 14 (最后 2 天 = zoo) |

### 5. 教训

#### 做对的

**Schema-first sub-agent 设计**. pydantic 校验过的 JSON 输出彻底消除 prompt injection 风险, 也让 orchestrator 行为可预测. v1.2.2 的 whale-analyst schema 漂移 bug 之所以能被发现, 全靠 pydantic 强制 fall-through 到默认值 — 没有这个机制, 静默丢失洞察可能持续几周.

**markdown memory 每个 agent 一份**. 改一个 `.md` 文件下次 report 就生效, 不用重新部署. 这是迭代速度最大的倍增器. 从 25 → 50+ memory 文件代表用户 5 年 G:\stocks 研究蒸馏成的 prompt context — 改它们是 `git diff` 不是工程.

**Bench-driven 因子选择**. 硬编码 `PRODUCTION_TOP10` 撑了一周. v1.4.2 换成滚动 bench top-N 后, 系统能跟着 regime 变化走, 不用手动改 prompt. 代价是每周一次 cron; 收益是 prompt 永不腐烂.

**Tushare 原始 HTTP > tushare 库**. 官方包的 round-robin DNS 在 Windows 上时不时挂死. 5 行 `requests.post` 直连 `http://api.tushare.pro` 跑得稳如老狗. 有时候最简单的 client 就是对的 client.

#### 坑过的

**sample30 overfit**. 第一版 zoo bench 用 30 只精选股, 因为几秒就跑完. top 信号 `qlib_CNTN60` 在 sample30 上 `rank_IR = -0.605`, 到 CSI300 上崩到 `-0.100` — **80% 衰减**. 还好我们在用于生产之前发现了. **教训: 不要相信任何 <100 股 bench 出来的结果**.

**Windows 编码到处是坑**.
- cmd.exe 把 utf-8 转 GBK (v1.2.1).
- Twine + Rich 进度条在 GBK 控制台上崩溃 (v0.6.0).
- pandas `pct_change(fill_method=)` FutureWarning 污染 bench 输出 (v1.3.5).
- 每个根因排查 30 分钟. **Windows 是一等公民**, 不要假设默认是 utf-8.

**Operator 静默失败**. zoo 建设里发生过三次: 某个 alpha 注册了但运行时崩, 因为 operator 没 import (`product` / `log` / `indneutralize`). pydantic 捕不到, 因为 alpha 在 bench 之前都不会被调用. 后来在 bench 输出里加了 `compute_error` 状态字段, 这类问题才能被发现 — 但更好的做法是 lint imports 对齐 operator 目录. 计划 v1.5.x 收拾.

#### 事后看是过度设计的

- dream loop (`OutcomeTracker → Introspector`) 概念优雅但当前规模用得少. 等历史报告积累够了再放光. 现在是占位.
- 29-preset swarm 散弹 (Vibe-Trading 的做法). 我们 4 个深 preset 就够了. **Preset 数量是虚荣, preset 深度才是触达**.

### 6. 下一步往哪走

按用户价值 × 成本粗排:

1. **Pre-ST 红线筛子** (S, A 股专属): 4 条规则的 ST 候选预警 (连续亏损 / 审计意见 / 净资产 / 分红缺口). 接 `bear-advocate` 和 `morning-brief-writer`. v1.5 主打.
2. **TDX 公式导出** (S, 零售落地): top-pick → 通达信选股公式字符串, 用户复制粘贴进券商. 闭环 "研报 → 实盘".
3. **2025-Q1 样本外 bench**: 用全新季度数据重跑 440 因子, 验证波动率主题是否稳.
4. **`BenchmarkLoader` + iterative compute** (M): 解锁 `gtja143` 和 `gtja149`, 目录 100% 完结.
5. **Shadow account 分析**: 解析 同花顺/东方财富 对账单, KMeans 聚类 round-trip → 隐含规则 → shadow 回测. 新颖功能, 需要用户交易历史验证.

### 7. 新贡献者快查

```bash
# 安装
pip install financial-analyst
financial-analyst doctor                     # 环境体检

# 数据
financial-analyst industry refresh           # 一次性, Tushare → 行业缓存
financial-analyst news-collect --sources kuaixun,longhu --limit 200

# 因子
financial-analyst alpha list                 # 全 440 个
financial-analyst alpha show alpha089        # 公式 + 论文 + 描述
financial-analyst alpha bench --universe csi300_active \
    --since 2024-06-01 --until 2024-12-31 --save
financial-analyst alpha snapshot auto --universe csi300_active \
    --until 2024-12-31 --top-n 20

# 研报
financial-analyst report SH600519 --asof 2024-12-31
financial-analyst ask "为什么茅台技术面这么弱"
financial-analyst morning-brief
financial-analyst mainline-classify
```

Memory 在 `memories/<agent>/*.md`. Sub-agent 在 `src/financial_analyst/agent/tier{1,2,3}/`. 因子在 `src/financial_analyst/factors/zoo/{alpha101,gtja191,qlib158}/`.

**贡献方式: 改 markdown 就够了. 基建已经完成**.

<div align="right">

[⬆ 返回顶部](#financial-analyst--build-journey--architecture-overview) ｜ [Switch to English](#english)

</div>

---

*Two weeks, 440 alphas, 21 sub-agents, 12 PyPI releases. Apache-2.0 open source at https://github.com/jesson-hh/financial-analyst*

*两周, 440 因子, 21 sub-agent, 12 个 PyPI 版本. Apache-2.0 开源, https://github.com/jesson-hh/financial-analyst*
