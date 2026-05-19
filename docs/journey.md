# financial-analyst — Build Journey & Architecture Overview

A two-week build log of `financial-analyst`, an A-share single-stock
deep-dive multi-agent research workstation. From empty repo to v1.4.2
on PyPI with 440 quantitative alphas and 21 sub-agents.

---

## 1. What it is, in one paragraph

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

---

## 2. Architecture — three trust tiers + plug-in everything

### Trust isolation

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

### Pluggable everywhere

- **Sub-agents**: drop a `.py` file under `config/plugins.yaml` to
  register a new agent. Discovered at CLI startup.
- **LLM providers**: LiteLLM under the hood; configure
  `config/llm.yaml` to switch between Anthropic / OpenAI / Qwen /
  DeepSeek / Ollama (and per-agent overrides, so e.g. `whale-analyst`
  can use Qwen-Plus while `report-writer` uses Sonnet).
- **Data loaders**: `BaseLoader` ABC. Ship `TushareLoader` (raw HTTP)
  and `QlibBinaryLoader`. Drop in CSV / Parquet / your own.
- **Models**: `ModelRegistry` for ML predictors. Plug in LGB, FM
  cluster, TSFM models.
- **Memories**: per-agent markdown under `memories/<agent>/*.md`.
  Edit a file → next agent invocation picks it up. SQLite FTS5
  full-text retrieval; falls back to `load_all()` when search returns
  thin results.
- **Alphas**: register via `AlphaSpec` decorator in
  `factors/zoo/{family}/alphas.py`. The 440-alpha zoo is just the
  curated default.

---

## 3. Build journey — chronological

### Phase 0: Design & scaffolding (Day −2 to 0)

Studied two open-source agent frameworks:

- **HKUDS/Vibe-Trading**: A multi-market quant agent with 29 swarm
  presets, 452 formulaic alphas, and a `alpha bench` CLI. We borrowed
  the zoo architecture and CLI shape.
- **anthropics/financial-services**: 3-tier trust isolation and
  schema-locked sub-agent JSON. We borrowed the trust model and
  pydantic discipline.

What we kept vs. what we changed: kept the trust tiers, kept the
zoo registry pattern, dropped the 29-preset shotgun (our 4 are
deeper), dropped the live-trading bias (we're a research tool, not
a bot), added the Chinese A-share specificity (Tushare loader, 申万
industry classifier, xueqiu sentiment, 14 R7-R20 sentiment signals
from the user's 5-year G:\stocks research).

### Phase 1: Foundation (v0.1.0 – v0.6.0)

- **v0.1-0.4**: CLI + TUI shell, 13 sub-agents, YAML swarm presets,
  pluggable memory, FTS5 retrieval, dream loop, MCP server, ask-agent.
- **v0.5**: natural-language router (`ask` command, 6 tools).
- **v0.6**: pypi publish. First public release.

### Phase 2: Data integrations (v1.0.0 – v1.2.2)

- **v1.0**: Docker + README polish + RELEASE_NOTES.
- **v1.1**: OpenCLI integration → local NewsDB. 4 collectors
  (eastmoney 7×24 / 龙虎榜 / 十大流通股东 / sinafinance 7×24) into
  SQLite with FTS5.
- **v1.2.0**: xueqiu cookie-mode collectors (3 more: comments / hot
  stocks / earnings dates). Whale-analyst now reads `social_posts`
  for retail sentiment.
- **v1.2.1** (HOTFIX): caught Windows cmd.exe transcoding bug —
  every Chinese character in the NewsDB was silently `���` mojibake
  because `subprocess` with `shell=True` ran node's utf-8 stdout
  through the GBK console code page. Fix: parse the npm `.CMD` shim,
  call `node <main.js>` directly with `shell=False`.
- **v1.2.2** (HOTFIX): two more bugs landed together:
  1. `social_posts` dedup collapse — xueqiu's `{author, text, url}`
     items had no `id` field; the upsert's `INSERT OR REPLACE` key
     collapsed all 30 comments to one row.
  2. `whale-analyst` schema drift — SYSTEM_PROMPT listed the policy
     but not the JSON schema; LLM hallucinated its own keys, pydantic
     silently dropped them, retail-sentiment insights never reached
     `report-writer`.

### Phase 3: Alpha Zoo (v1.3.0 – v1.4.2) — the 2-day push

This is where the project changed scale. We started v1.3.0 with **22
hand-picked alphas** and ended v1.4.2 with **440 alphas across three
reference catalogues** — a 20× expansion in two days.

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

#### Key zoo decisions

**Architecture choices**:
- `compute(panel) → pd.Series` stateless API for every alpha. No
  cross-call state. Trivially parallelisable later.
- Per-code groupby for every time-series operator (`ts_max`, `delta`,
  `correlation`, etc.) so windows never bleed across stocks.
- `min_periods=window` everywhere → alphas never emit partial-window
  signals.
- `MultiIndex(datetime, code)` panel. Cross-sectional ops
  (`rank`, `scale`, `indneutralize`) group by `datetime`; time-series
  ops group by `code`.

**Operator catalogue**:
- 30+ operators total: `rank`, `ts_sum/mean/max/min`, `ts_rank`,
  `ts_argmax/argmin`, `delta`, `delay`, `correlation`, `covariance`,
  `decay_linear`, `wma`, `sma` (GTJA-style EWMA), `signedpower`,
  `scale`, `indneutralize`, plus regression triplet
  `regbeta / regresi / rsqr` and `sequence` (synthetic time index).

**Three families ported**:
- **alpha101**: WorldQuant 101 Formulaic Alphas (Kakushadze 2015,
  arXiv:1601.00991). 101/101 = **100%**.
- **gtja191**: Guotai Junan Securities 191 Alphas (国泰君安 2017).
  189/191 = **99%**. The 2 unportable are `gtja143` (recursive
  `SELF`) and `gtja149` (benchmark-index beta).
- **qlib158**: Microsoft Qlib Alpha158. 150 features — many window
  variants of the same underlying feature, so 95% practical coverage.

**Bench / IC analysis**:
- `alpha bench --universe X --since Y --until Z --fwd-days 5` computes
  cross-sectional rank-IC of each alpha against forward 5-day returns.
- Sorted by `|rank_IR|` descending; output includes `ic`, `rank_ic`,
  `ir`, `rank_ir`, `hit_rate`, `n_dates`, `n_obs`.
- 440-alpha bench on 868 CSI300 codes × 144 days runs in **2m 43s**.

**Key empirical findings from real CSI300 bench (2024-H2)**:
- `qlib158` family dominates: **30% of qlib158 alphas have
  |rank_IR| > 0.25** vs 20% for gtja191 vs 12% for alpha101.
- **Volatility-based features rule** on CSI300 2024-H2:
  `qlib_VSTD60 (+0.54)`, `qlib_STD10 (-0.42)`, `gtja095 (-0.43)`,
  `qlib_KLEN (-0.35)` — five of top eight.
- **Sample30 (30 stocks) overfits badly**: sample30 leaders lost
  44-100% of their signal magnitude on csi300. Rule of thumb: trust
  nothing tested on <100 stocks.

#### Integration story

- **v1.3.4**: built `alpha snapshot` CLI → cached parquet → factor-computer
  lookup → quant-analyst sees `zoo_signals` block. Initially used a
  hardcoded `PRODUCTION_TOP10` curated list (`qlib_VSTD60, gtja095,
  qlib_STD10, ...`) with a fixed sign convention table in SYSTEM_PROMPT.
- **v1.4.2**: replaced the hardcoded list with **dynamic top-N
  selection** from the latest cached bench. Snapshot rows now carry
  `bench_rank_ic / bench_hit_rate / bench_n_dates` so the LLM can
  interpret each alpha's direction from its bench-validated sign,
  not from a hardcoded prompt section.
  - Workflow: weekly cron runs `alpha bench --save` then
    `alpha snapshot auto --top-n 20`.
  - Reports auto-pick up the new top-20.
  - LLM verified output (SH600519, 2024-12-31):
    "qlib_WVMA60 rank_pct=91.1% with bench_rank_ic=+0.052
    (positive-class) → bullish reading from this alpha."

### Phase 4 (future): unblockers for the last 2 alphas

- **gtja143**: recursive `SELF` reference. Needs an optional
  `compute_iterative(panel, state) → (series, state)` API. Planned
  for v1.5.x.
- **gtja149**: benchmark-relative beta. Needs `BenchmarkLoader` to
  carry CSI300 close as a parallel series. Planned for v1.5.x.

---

## 4. Numbers at a glance

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

---

## 5. Lessons learned

### What worked

**Schema-first sub-agent design.** Pydantic-validated JSON outputs
eliminate prompt-injection risk and make the orchestrator
deterministic. The schema-drift bug in `whale-analyst` (v1.2.2) was
caught only because pydantic enforced fall-through to defaults —
without that, the silent insight loss would have lasted weeks.

**Per-agent memory as markdown.** Editing a `.md` file and seeing the
next report pick up the new rule, without redeploying, is the single
biggest contributor to fast iteration. The 25 → 50+ memory files
represent the user's 5-year G:\stocks research distilled into prompt
context — and changing them is `git diff` not engineering.

**Bench-driven alpha selection.** Hardcoding `PRODUCTION_TOP10` worked
for a week. Replacing it with dynamic top-N from a rolling bench
(v1.4.2) means the system tracks regime shifts without manual prompt
edits. The cost is one weekly cron; the benefit is no prompt rot.

**Tushare raw HTTP > tushare library.** The official package's
round-robin DNS hung intermittently on Windows. A 5-line `requests.post`
to `http://api.tushare.pro` ran flawlessly. Sometimes the simplest
client is the right client.

### What hurt

**Sample30 overfitting**. The first zoo bench was on 30 hand-picked
stocks because it ran in seconds. Top signals like `qlib_CNTN60` had
`rank_IR = -0.605` on sample30, then collapsed to `-0.100` on
CSI300 — an 80% degradation. We caught it before relying on the
results in prod, but rule-of-thumb learned: **trust nothing tested on
<100 stocks**.

**Windows encoding everywhere**.
- cmd.exe transcoding utf-8 → GBK in `subprocess` shell=True (v1.2.1).
- Twine + Rich progress bar crashing on `•` in GBK console (v0.6.0).
- pandas FutureWarning on `pct_change(fill_method=)` polluting bench
  output (v1.3.5).
- Each took ~30 minutes to root-cause and fix. **Windows is a
  first-class environment**; assume nothing is utf-8.

**Silent operator failures**. Three times in the zoo build, an alpha
was registered but its compute function failed at runtime because an
operator wasn't imported (`product`, `log`, `indneutralize`). Pydantic
caught nothing because the alpha was never called until bench time.
The `compute_error` status in bench output was added specifically to
make these visible — but better: we now lint imports against the
operator catalogue. (Planned cleanup, v1.5.x.)

### What was over-engineered (in hindsight)

- The dream loop (`OutcomeTracker → Introspector`) is conceptually
  elegant but underused at current scale. Will become valuable when
  there's enough report history to detect patterns.
- 29-preset swarm shotgun (Vibe-Trading's approach). We have 4 deep
  presets and they're enough. Preset count is vanity; preset depth
  is reach.

---

## 6. Where to next

In rough priority order, based on user value × cost:

1. **Pre-ST regulatory filter** (S, A-share-specific): four-rule
   screener for ST candidates (consecutive losses, audit opinion,
   net assets, dividend gap). Wires into `bear-advocate` and
   `morning-brief-writer`. Likely v1.5.
2. **TDX formula export** (S, last-mile retail): top-picks → 通达信
   selection-formula string the user can paste into their broker.
   Closes the research-to-execution gap.
3. **2025-Q1 out-of-sample bench**: rerun the 440-alpha zoo on a
   fresh quarter to confirm the volatility-theme finding holds.
4. **`BenchmarkLoader` + iterative compute** (M): unlocks `gtja143`
   and `gtja149`, closes the catalogue to 100%.
5. **Shadow-account analysis**: parse 同花顺 / 东方财富 trade exports,
   KMeans-cluster round-trips into implicit rules, replay as shadow
   backtest. Novel feature; needs user trade history to validate.

---

## 7. Quick reference for new contributors

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

Memories live under `memories/<agent>/*.md`. Sub-agents are
`src/financial_analyst/agent/tier{1,2,3}/`. Alphas are
`src/financial_analyst/factors/zoo/{alpha101,gtja191,qlib158}/`.

**Contribute by editing markdown. The infrastructure is done.**

---

*Built two weeks, 440 alphas, 21 sub-agents, 12 PyPI releases. Open
source under Apache-2.0 at https://github.com/jesson-hh/financial-analyst.*
