# Changelog

## v1.3.3 — 2026-05-19

### Added — regression operators (regbeta / regresi / rsqr / sequence / wma)
- `regbeta(y, x, n)` — rolling OLS β over the last n bars per code
- `regresi(y, x, n)` — rolling OLS residual `y - (βx + α)`
- `rsqr(y, x, n)` — rolling OLS R², in [0, 1]
- `sequence(panel_template, n)` — synthetic time-index series (1, 2, 3, ...
  per code), so `regbeta(close, sequence, N)` computes the slope of close
  against time. Matches GTJA-191's `SEQUENCE(N)` notation.
- `wma(x, n)` — linear-weighted MA (alias of `decay_linear` for formula
  fidelity)
- `max_pair / min_pair` — element-wise max/min, named to disambiguate
  from `ts_max / ts_min` (time-series ops)

These unlock the regression-based half of all three families.

### Added — 38 more alphas (zoo: 104 → 142)
- **alpha101 +11 → 42**: `041` (sqrt(high·low) - vwap), `042` (rank-skew
  on VWAP), `043` (vol-rank × neg-momentum rank), `044`, `045`, `049`
  (slope-reversal regime switch), `050`, `052`, `053`, `054`, `055`.
- **gtja191 +6 → 44**: `gtja021` (slope of MA6 via REGBETA), `gtja027`
  (WMA of 3d+6d returns), `gtja076` (CV of return-per-volume), `gtja095`
  (20d std of dollar volume), `gtja128` (MFI-style typical-price volume
  ratio), `gtja160` (down-day-only volatility EWMA).
- **qlib158 +21 → 56**:
  - BETA/RSQR/RESI × {5,10,20,60} = 12 new trend-regression features
  - VMA/VSTD/VSUMP × {5,20,60} = 9 new volume statistics

### Top signals on sample30 (2024-06 to 2024-12, fwd_5d)
The v1.3.3 regression operators paid off — 3 of the top 7 are new:

```
qlib_CNTP60  rank_IR=-0.605  (60d up-day count, reversal)
qlib_ROC60   rank_IR=+0.592
qlib_CNTN60  rank_IR=+0.531
qlib_RSQR60  rank_IR=-0.508  ← new (60d trend linearity)
qlib_BETA60  rank_IR=-0.431  ← new (60d trend slope)
gtja076      rank_IR=-0.330
qlib_RESI60  rank_IR=+0.278  ← new (60d trend-residual)
```

The clean interpretation: in 2024-H2 on A-share large caps, **strong
linear 60-day trends predicted reversal**. RSQR60 and BETA60 both
negative-rank-IR confirms this from two angles (R² magnitude, slope
magnitude), and RESI60 positive-rank-IR is its complement (large
residuals = away-from-trend = mean-revert toward trend).

### Tests
- Count baselines bumped to v1.3.3 (alpha101 ≥ 42, gtja191 ≥ 44,
  qlib158 ≥ 56). 14 zoo tests pass unchanged.

### Roadmap
- alpha101 remaining: 59 alphas. The next batch needs *industry
  neutralisation* (`indneutralize`) — `alpha48`, `56`, `58`, `59`, `63`,
  `67`, `69`, `70`, `76`, `79`, `80`, `82`, `87`, `89`, `90`, `91`, `93`,
  `97`, `100`, `101` all use `IndNeutralize(...)`. Industry classifier
  loader is the v1.3.4 prerequisite.
- gtja191 remaining: 147 alphas.
- qlib158 remaining: 102 alphas (mostly WVMA / SUMD-style which are
  doable with existing ops).

## v1.3.2 — 2026-05-19

### Added — qlib158 family (35 first-batch alphas)
New family `qlib158` ports the simple OHLC-ratio + moving-stat + stochastic
features from Microsoft Qlib's `Alpha158` handler. v1.3.2 ships the first 35
of 158:

- **6 candle shape**: `qlib_KMID`, `qlib_KLEN`, `qlib_KMID2`, `qlib_KUP`,
  `qlib_KLOW`, `qlib_KSFT` — body/wick/range ratios.
- **12 MA/STD/ROC**: `qlib_MA{5,10,20,60}`, `qlib_STD{5,10,20,60}`,
  `qlib_ROC{5,10,20,60}` — relative MA, dispersion, lagged-close ratios.
- **9 stochastic / argmax-argmin**: `qlib_RSV{5,10,20}`,
  `qlib_IMAX{5,10,20}`, `qlib_IMIN{5,10,20}` — %K position, high-recency,
  low-recency.
- **6 up/down counts**: `qlib_CNTP{5,20,60}`, `qlib_CNTN{5,20,60}` —
  positive/negative day fractions.
- **2 price-vol correlations**: `qlib_CORR{5,20}` —
  `correlation(close, log(volume), N)`.

### Added — 20 more alphas across alpha101 + gtja191
- **alpha101 +9 → 31 total**: `alpha017`, `023`, `026`, `028`, `030`,
  `033`, `034`, `035`, `040`.
- **gtja191 +11 → 38 total**: `gtja022`, `024`, `029`, `031`, `034`,
  `038`, `040`, `046`, `054`, `057`, `065`.

**Zoo now ships 104 alphas total** (was 49 in v1.3.1).

### Fixed
- `config/universes/sample30.txt` had `SH000858` (五粮液); correct prefix
  is `SZ000858`. Bench output silently dropped this code as "empty";
  fixing brings the sample universe back to 30 codes.

### Top signals on sample30 (2024-06 to 2024-12, fwd_5d)
The qlib158 family dominates the leaderboard once added:

```
qlib_CNTN60  -0.651   ← new strongest signal in entire zoo
qlib_CNTP60  +0.599
qlib_RSV20   +0.579
alpha005     -0.262
qlib_STD60   -0.237
gtja008      -0.233
gtja001      -0.225
```

The CNTN/CNTP findings re-confirm the well-known "high recent down-day
count ⇒ mean-reversion bounce" effect on A-share large caps. n_dates
is smaller (~80) because of the 60-day window.

### Tests
- Count assertions bumped to v1.3.2 baseline (alpha101 ≥ 31,
  gtja191 ≥ 38, qlib158 ≥ 35).
- Existing 14 tests pass unchanged.

### Roadmap
- alpha101 remaining: 70 alphas (mostly the regression-based ones —
  REGBETA, RESIDUAL — which need a new linear-regression operator first).
- gtja191 remaining: 153 alphas.
- qlib158 remaining: 123 (including the heavier BETA/RSQR/RESI regression
  features which need the same operator as alpha101's regression
  variants).

## v1.3.1 — 2026-05-19

### Added — 27 more alpha ports
- **alpha101**: +12 → 22 total. Added `alpha005`, `008-011`, `016`, `018-020`,
  `022`, `024`, `025`. The new strongest signal on `sample30` (2024-06 to
  2024-12) is `alpha005` with `|rank_IR|=0.262` (open vs 10d-VWAP rank
  weighted by negative `|close-VWAP rank|`).
- **gtja191**: +15 → 27 total. Added `gtja006`, `008`, `010`, `011`, `013`,
  `017`, `019`, `020`, `025`, `028`, `037`, `047`, `052`, `058`, `068`.
  `gtja008` (mid+VWAP blend 4-day rank-change) jumps to `|rank_IR|=0.233`,
  second-strongest in the entire zoo.

Zoo now ships **49 alphas total** (was 22 in v1.3.0).

### Changed — cleaner bench output
- `bench_runner` traps `numpy.RuntimeWarning: invalid value encountered in
  divide` (raised by `numpy.corrcoef` whenever a rolling window has zero
  variance, which is expected behaviour on quiet days) so the CLI table is
  no longer drowned in noise.
- `PanelData.returns` now passes `fill_method=None` to silence pandas
  `pct_change` `FutureWarning` (also a correctness improvement: a
  suspended trading day no longer forward-fills as a zero-return day).

### Tests
- Synthetic bench panel extended from 60 → 300 days to cover the deep-
  history alphas (`alpha019`, `alpha024`, `gtja025` reach back 100-250 days).
- New count assertions lock the v1.3.1 baseline so future patches must
  preserve at least 22 alpha101 / 27 gtja191.

### Notes for v1.3.x roadmap
- Remaining: 79 alpha101 + 164 gtja191 (formula text confirmed; just
  ports). Patch releases will bring 15-20 alphas at a time.
- `qlib158` family stub deferred to v1.3.x — direct Qlib `Alpha158`
  re-export needs `D.features()` semantics that don't fit our `PanelData`
  yet. Working on a thin adapter.

## v1.3.0 — 2026-05-19

### Added — Alpha Zoo (inspired by HKUDS/Vibe-Trading)
A registry of named alpha formulas with a `alpha bench` CLI that emits
IC / IR / hit-rate per alpha against a chosen universe and period.
Two families ship in this release:

- **`alpha101`** — 10 of the most-cited WorldQuant 101 Formulaic Alphas
  (Kakushadze 2015, arXiv:1601.00991): alpha001-004, 006, 007, 012-015.
- **`gtja191`** — 12 of the most-cited Guotai Junan 191 Alphas (国泰君安
  2017), designed specifically for A-share short-horizon prediction:
  gtja001-005, 007, 009, 012, 014, 018, 042, 053.

Three CLI commands:
- `financial-analyst alpha list [family]` — Rich table of names + descriptions
- `financial-analyst alpha show <name>` — formula text + paper citation
- `financial-analyst alpha bench <family> --universe <path|name> --since <date> --until <date> [--fwd-days N] [--top K]`

Bench output is sorted by `|rank_IR|` descending. Verified end-to-end on
30 A-share large caps × 138 trading days: `gtja001 rank_IR=-0.225`,
`gtja014 rank_IR=+0.201` etc.

### Added — sample30 universe
`config/universes/sample30.txt` — 30 hand-picked A-share large caps
(Maotai/Wuliangye/Ping An/CATL/BYD/Hikvision/etc) so `alpha bench
--universe sample30` works out-of-box with no additional setup.

### Added — `financial_analyst.factors.zoo` package
Public API: `register`, `get`, `list_alphas`, `families`, `PanelData`,
`run_bench`, `bench_one`. Operators (`rank`, `ts_rank`, `delta`,
`correlation`, `decay_linear`, `sma`, etc.) live in
`factors.zoo.operators`. All `ts_*` ops use `min_periods=window` so
alphas never emit partial-window signals — full look-ahead protection
on shipped alphas.

User-supplied alphas register via `register(AlphaSpec(...))` from any
plugin under `~/.financial-analyst/plugins/`.

### Tests
- `tests/test_factor_zoo.py` — 14 new tests covering registry, panel
  alias normalisation, operator semantics, and end-to-end bench. Total
  package test count now 335+.

### Docs
- `docs/alpha_zoo.md` — full reference: CLI usage, operator catalogue,
  how to add your own alpha, how the bench loop works.

### Known limitations (rolling forward in 1.3.x patches)
- Only 22 / 292 alphas ported in v1.3.0; remaining alphas land in 1.3.x
  patches.
- `qlib158` and `academic` families are placeholders.
- Daily-bar panel only; 5min support is a future PanelData extension.

## v1.2.2 — 2026-05-19

### Fixed
- **xueqiu social_posts dedup collapse**. The opencli xueqiu/comments
  adapter returns items shaped `{author, text, likes, replies, retweets,
  created_at, url}` with no explicit `id`. The earlier upsert chain only
  consulted `id` / `post_id` / `ts`, so every row in a 30-comment batch
  hashed to `xueqiu_comments::SH600519::` and `INSERT OR REPLACE` left
  only the last item alive. Net effect on v1.2.0 / v1.2.1: every
  `news-collect --sources xueqiu-comments` call wrote exactly **1 row**
  no matter how many comments came back.
- Fix: extend the post_id fallback chain to consult `url` (xueqiu's
  unique per-post URL) and `created_at`. Also map `replies` →
  `comments_count` to match xueqiu's field name.
- **whale-analyst dropped all retail-sentiment insight**. SYSTEM_PROMPT
  enumerated the policy (14 S/SS signals, score aggregation rules) but
  never listed the WhaleOutput JSON schema. The LLM hallucinated its own
  keys (`ticker`, `whale_judge`, `analyst_note`, `playbook_v_anchors`
  etc); pydantic silently dropped them and used defaults, so the
  `bull_points` / `bear_points` / `alerts` lists arrived at
  `report-writer` empty even when the LLM had read 雪球 posts and
  formed an opinion on them.
- Fix: spell out the exact JSON schema in SYSTEM_PROMPT with hard rules
  ("Use the EXACT keys", "If 雪球 social posts are supplied, you MUST
  surface their signal in bull/bear or alerts"). Verified that the
  WhaleAnalyst paragraph in the SH600519 report now reads e.g.
  「雪球高赞帖文（102赞/86评）集中引用段永平长期持有框架」.

### Changed
- `whale-analyst` social_posts lookback widened from 7 → 30 days.
  xueqiu activity for any single stock is bursty; a 7-day window
  frequently misses the latest discussion wave even for liquid names.

### Verification
- Cleared `social_posts`, re-collected 30 SH600519 xueqiu comments →
  30 distinct rows in DB with intact Chinese, `replies` mapped to
  `comments_count`, url-based unique IDs.
- New regression test `test_social_posts_real_xueqiu_schema` feeds the
  exact upstream payload shape to lock in the fix.

If you were on v1.2.0 / v1.2.1 and collected xueqiu data, **re-run the
collection** — only the last comment per stock survived in your DB:
```bash
python -c "from financial_analyst.data.news_db import NewsDB; \
db=NewsDB(); db.conn.execute('DELETE FROM social_posts'); \
db.conn.commit(); db.close()"
financial-analyst news-collect --sources xueqiu-comments --code SH600519 --limit 30
```

## v1.2.1 — 2026-05-19

### Fixed
- **Windows utf-8 mojibake** in NewsDB. Calling `opencli.CMD` via `subprocess`
  + `shell=True` routed the node child's stdout through `cmd.exe`, which
  transcoded utf-8 → the active console code page (GBK / cp936 on a Chinese
  Windows). Result: all Chinese characters in collected news / 龙虎榜 / 十大股东
  were stored as `���` mojibake.
- Fix: parse the npm-generated `.CMD` shim to recover the underlying
  `main.js` path, then call `node <main.js> ...` directly with `shell=False`.
  cmd.exe is no longer in the loop and utf-8 reaches Python unchanged.
- New regression tests: `test_run_opencli_decodes_utf8_chinese` round-trips
  Chinese through the bytes-mode pipe, and `test_resolve_npm_shim_parses_main_js`
  locks in the .CMD parser against the actual npm-generated wrapper format.

If you were on v1.1.0 / v1.2.0 on Windows, **rebuild your NewsDB**:
```bash
python -c "from financial_analyst.data.news_db import NewsDB; \
db=NewsDB(); c=db.conn; [c.execute(f'DELETE FROM {t}') for t in \
['news','lhb','holders','social_posts','hot_stocks','earnings_dates']]; \
c.commit(); db.close()"
financial-analyst news-collect --sources kuaixun,longhu,sinafinance --limit 500
```
Linux / macOS users were not affected.

## v1.2.0 — 2026-05-18

### Added
- **3 xueqiu cookie-mode collectors**: `xueqiu-comments` (散户讨论), `xueqiu-hot` (热股榜), `xueqiu-earnings` (财报日历)
- NewsDB extended with 3 new tables: `social_posts`, `hot_stocks`, `earnings_dates`
- `news-collect --sources xueqiu-comments --code SH600519` etc
- `financial-analyst doctor` command for env diagnostics (OpenCLI / Chrome / NewsDB / loaders)
- `whale-analyst` sub-agent now pulls retail sentiment from `social_posts` when available

### Requirements
xueqiu commands need OpenCLI Chrome extension + Chrome login on xueqiu.com. See [docs/xueqiu_setup.md](docs/xueqiu_setup.md).

### Why this matters
Tushare and other APIs can't access retail-investor discussion. Xueqiu is the largest Chinese stock community — its `social_posts` give whale-analyst access to crowd sentiment that quantitative signals miss. Initial use case: validate that 主力 OBV trend matches retail engagement (or detect divergence).

## v1.1.0 — 2026-05-18

### Added (OpenCLI integration → local news DB)
- **NewsDB** at `~/.financial-analyst/data/news.sqlite` with `news`, `lhb`, `holders` tables + FTS5 full-text index
- **4 OpenCLI collectors** for eastmoney 7x24 快讯 + 龙虎榜 + 十大流通股东 + sinafinance 7x24
- **3 CLI commands**: `news-collect`, `news-query`, `news-stats`
- `news-reader` + `f10-reader` sub-agents now augment from NewsDB when drop-zone is sparse

### Use case
Daily cron / scheduled task:
```bash
financial-analyst news-collect --sources kuaixun,longhu --limit 500
```
Then every `financial-analyst report SH600519` automatically has the latest news + 龙虎榜 + 股东 context — without consuming LLM tokens to scrape.

### Requirements
- `npm install -g @jackwener/opencli` (Node.js >= 21)
- Collectors are PUBLIC (no login). xueqiu cookies-based ones reserved for v1.2.

## v1.0.0 — 2026-05-18

### Added
- **Docker support**: `Dockerfile` + `docker-compose.yml` for zero-config deployment.
- **README polish**: three install paths (PyPI / Docker / source), all 13 CLI commands documented in quick-start.
- **Badges**: PyPI version, Python compat, tests, license, status.

### Changed
- Bumped version to **1.0.0** — stable API.
- README quick-start rewritten to highlight Docker as 2-minute path.
- `Development Status` classifier updated to `5 - Production/Stable`.

### Stability promise from 1.0
- All public APIs (`BaseLoader`, `BaseModel`, `BaseIngester`, `BaseNewsCollector`, `BaseF10Collector`, `KnowledgeBase`, `SubAgent`, registries, CLI subcommands) follow semver from here.
- Breaking changes require major version bump.
- v1.x will focus on stability + ecosystem (additional collectors / models / docs), not protocol changes.

### Capabilities at 1.0
- 13 single-stock sub-agents in three trust tiers + 5 market-level agents + introspector + ask-agent = 20 agents total
- 7 swarm presets: stock-deep-dive, mainline-radar, morning-brief, intraday-review, dream (implicit)
- 12 MCP tools exposed for Claude Desktop integration
- 11 CLI subcommands (report / ask / ingest / dream / mainline / brief / intraday / models / loaders / agents / collectors / version)
- 290 tests + 1 opt-in real E2E test
- Memory system: per-agent + _shared + always_include + FTS5 retrieval + hot reload + dream-loop proposals
- BYOM via `config/plugins.yaml` — register your private models / loaders / collectors without forking

## v0.10.0 — 2026-05-18

### Added (MCP Server)
- `src/financial_analyst/mcp_server.py` — MCP stdio server exposing 12 tools to Claude Desktop / Claude Code / OpenClaw.
- Tools: `ask`, `quick_quote`, `quick_factors`, `memory_search`, `list_past_reports`, `read_past_report`, `list_dream_proposals`, `report`, `mainline`, `brief`, `intraday`, `dream`.
- `financial-analyst-mcp` console script entry point registered in `pyproject.toml`.
- `docs/mcp.md` — setup guide + tool reference + security model + troubleshooting.
- `tests/test_mcp_server.py` — 10 unit tests covering tool registry, dispatch, and schema validation.
- `mcp>=1.0` added to dependencies.

### Changed
- Version bump 0.6.0 → 0.10.0.
- README: added MCP Server section + updated test count.

## v0.6.0 — 2026-05-18

### Added
- First PyPI release. Install: `pip install financial-analyst`.
- Polished `pyproject.toml` with full metadata (classifiers, urls, keywords, authors).

### Changed
- README quick-start lead now shows `pip install financial-analyst` instead of `git clone`.
- Version bump 0.5.0 → 0.6.0.

### Notes
- No functional code changes vs v0.5.0. This release is packaging-only.

## v0.4.0 — 2026-05-18

### Added (BYOM: Bring Your Own Models)
- `BaseNewsCollector` ABC — plug-in interface for auto-collecting news into `news/<code>/` drop-zone (`data/collectors/news/base.py`).
- `BaseF10Collector` ABC — plug-in interface for F10 data (公司公告/龙虎榜/大宗交易) into `f10/<code>/` (`data/collectors/f10/base.py`).
- 4 example stubs under `examples/`:
  - `custom_model_fm_cluster.py` — FM cluster model pattern
  - `custom_loader_csv_only.py` — minimal CSV-backed `BaseLoader`
  - `custom_news_collector.py` — Tushare news API skeleton
  - `custom_f10_collector.py` — pytdx F10 skeleton
- Plugin discovery: `config/plugins.yaml` lists user `.py` files exec'd at startup (`src/financial_analyst/plugins.py`).
- CLI introspection: `financial-analyst {models,loaders,agents,collectors} list`.
- `docs/byom.md` — full Bring-Your-Own-Models guide.

### Changed
- README "Extending" section now points to BYOM workflow.
- `tests/test_agent_registry.py` no longer pollutes the `SubAgentRegistry`; fixtures clear it.

## v0.3.0 — 2026-05-18

### Added (Ingest + Dream Loop)
- **CSV → Qlib binary ingester** (`data/ingest/csv_ingester.py`) with both long-format and per-code-filename support, schema-configurable, ohlcv field mapping.
- `BaseIngester` ABC + reserved `AkshareIngester` / `YfinanceIngester` stubs for v0.4+.
- CLI: `financial-analyst ingest --source <name> [--dry-run]`.
- **Dream loop** for agent self-improving memory:
  - `OutcomeTracker` — measure T+5d/T+20d outcomes against past predictions in `out/*.json`, scoring verdict ∈ {correct, wrong, partial, pending}.
  - `Introspector` sub-agent — LLM-driven post-mortem analyst (NOT in stock-deep-dive preset).
  - `ProposalWriter` — writes `Introspector` proposals to `memories/_proposed/<agent>/<date>_<slug>.md` with YAML frontmatter.
  - `memories/introspector/introspector_rules.md` meta-rules (focus on wrong>partial>correct, 2/3-5/6+ confidence thresholds, target risk-officer when in doubt).
- CLI: `financial-analyst dream [--since 30] [--dry-run]`.
- TUI: `/dream`, `/memory list-proposals`, `/memory accept _proposed/<file>`, `/memory reject _proposed/<file>`.
- `docs/data_ingest.md`, `docs/dream_loop.md`.

### Changed
- `config/data_sources.yaml` template added for ingester config.
- Memory CLI usage strings updated to list all 11 subcommands.

### Safety
- Dream proposals require human review (no auto-merge); auto-accept is explicitly NOT implemented.
- `/memory accept` only operates on paths starting with `_proposed/`.

## v0.2.3 — 2026-05-18

### Fixed (Hotfix found during real SH600666 testing)
- **`AgentMemory.load_relevant` falls back to `load_all` on 0 FTS5 hits** — prevents agents going "blind" when the JSON-derived query doesn't match memory wording.
- **Per-agent `always_include.txt`** — listed files load unconditionally regardless of retrieval results. Initial entry: `memories/risk-officer/always_include.txt` lists `hard_rules.md` (game-capital veto must never be missed).
- **`report-writer` post-validation** — if `risk-officer.veto_flags` is non-empty OR `rating_overall ≤ 0`, `position_pct` is forced to 0 and `action` re-derived. Sanity-override notes appended to the markdown report.
- **`mv_tier` enum** — `fundamental-analyst.FundamentalOutput.mv_tier` changed from `str` to `Literal["large","mid","small"]`; pre-normalize Chinese variants (`中小盘`→`small`, `大盘`→`large`, etc.) before pydantic validation.

## v0.2.2 — 2026-05-18

### Added
- **5min bar support**. `QlibBinaryLoader` now accepts `dict` provider_uri with `day` + `5min` (+ optional `1min`) keys.
- `BaseLoader.fetch_quote` signature extended with `freq: str = "day"` (backward compatible).
- `factor-computer` auto-fetches 5min bars where available, activating:
  - **board_scorer v5 `seal_micro` dimension** (-3..+3): `seal_bar`, `seal_at_close`, `gap_open`, `open_count`.
  - **volume_regime R11 `tail_surge`** signal: last-30-min volume + return ramp.
  - **R14 super_distr** combined signal (`r9_distr AND r11_tail_surge`).
- TushareLoader gracefully returns empty DataFrame for non-day freq.

## v0.2.1 — 2026-05-18

### Added
- `ParquetCache` wired into `TushareLoader` (cache miss → API; cache hit → no network). Configurable TTL (default 86400s = 1 day) and `enable_cache=False` opt-out.
- `QlibBinaryLoader` reads Qlib binary directories — zero-network microsecond reads. Schema: `<provider_uri>/calendars/day.txt` + `instruments/all.txt` + `features/<code_lower>/<field>.day.bin` (4-byte float32 start_index + float32 array).
- `loader_factory.get_default_loader()` — reads `config/loaders.yaml` to instantiate the configured default. Sub-agents (`quote-fetcher`, `factor-computer`, `LGBMomentumModel`) use this factory.
- `config/loaders.yaml` template with both `tushare` (cache) and `qlib_binary` options.

### Changed
- `TushareLoader` re-implemented using raw `requests.post` (bypasses the `tushare` Python library's round-robin to `api.waditu.com` which times out behind corporate proxies). HTTP only.
- `quote-fetcher` uses `_safe_float` for `daily_basic` fields (handles None/NaN gracefully for stocks without dividends, etc.).
- `cli.py` calls `load_dotenv(override=True)` so `.env` overrides shell env vars (fixes Windows user-level TUSHARE_TOKEN conflicts).
- `llm.client` now explicitly passes `api_key` from per-provider `api_key_env` so LiteLLM doesn't fall back to OPENAI_API_KEY when using qwen/deepseek/etc.
- `config/llm.yaml` default switched to Qwen (`qwen3.5-plus`) since most users have DashScope keys, not Anthropic.
- Report writer renders the markdown report inline in terminal (Rich Markdown) and exports a colored HTML copy next to the .md. Clickable `file:///` URL in TUI output.
- Forced UTF-8 stdout/stderr at module load (cli.py + tui.py) so Windows zh-CN PowerShell doesn't choke on `¥` / emoji / rare CJK.

## v0.2.0 — 2026-05-18

### Added
- `MemoryIndex` — SQLite FTS5 full-text index over `memories/**/*.md` with CJK tokenization, incremental updates, agent-filtered search.
- `AgentMemory.load_relevant(query, top_k)` — hybrid retrieval that pulls top-K snippets via FTS5 while always including `_shared/` core rules. Backward-compatible with `load_all()`.
- Per-agent `memory_mode: full | retrieval` configuration in swarm preset YAML. Defaults to `full` (v0.1 behavior preserved).
- TUI `/memory` subcommands: `search`, `show`, `edit`, `stats`, `diff`, `reindex` (in addition to existing `list`, `reload`).
- `bear-advocate` and `risk-officer` opted into retrieval mode by default (biggest memory libraries).

### Changed
- `SubAgent.__init__` accepts optional `index: Optional[MemoryIndex] = None`.
- `swarm.load_preset()` accepts `memory_index` parameter; passes through to retrieval-mode agents only.
- `MemoryIndex.stats()` now includes `total_bytes` and `per_agent_bytes`.

### Token cost impact
- Single-stock report: ~80K → ~30K tokens (estimated 62% reduction) when both retrieval-mode agents are exercised.
- Per-report Qwen cost: ~¥0.05 → ~¥0.02.

## v0.1.0 — 2026-05-17

### Initial release
- 13 sub-agents in 3 trust tiers (5 Tier-1 data fetchers, 4 Tier-2 analysts, 4 Tier-3 decision agents).
- Pluggable per-agent memory (`memories/<agent>/*.md`) with `_shared/` cross-agent playbook.
- Tushare data loader, LGB momentum model, LiteLLM multi-provider abstraction.
- Rich TUI with prompt-toolkit REPL.
- 100+ pydantic-validated tests, opt-in real E2E test.
