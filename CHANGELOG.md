# Changelog

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
