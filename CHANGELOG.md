# Changelog

## v1.6.0 — 2026-05-20

### Added — Full-TUI BuddyApp (Claude Code-style layout)

`financial-analyst chat` now launches a full-screen prompt_toolkit
Application with a persistent input field. You can type the next
prompt while the agent is still thinking — it queues and runs after
the current turn finishes.

```
┌────────────────────────────────────────────────┐
│ Transcript (scrollable)                         │
│                                                  │
│ ❯ 茅台多少钱                                    │
│ ▶ quote_lookup({'code': 'SH600519'})            │
│ ✓ quote_lookup                                  │
│   SH600519: close=1280, PE=20.14...             │
│ 贵州茅台 现价 1280 元...                         │
├────────────────────────────────────────────────┤
│ ⠋ 调用 chain_for…  [ESC 取消] ▇▃▄▇▂ +0.8% #023  │ ← spinner row
├────────────────────────────────────────────────┤   (only when running)
│ ❯ 顺便看看比亚迪█                                │ ← persistent input
└────────────────────────────────────────────────┘
```

Behaviour:
- **Type any time**: the input field is always focused, even while the
  spinner animates. Press Enter to submit.
- **Submission queue**: if a turn is already running, your new submit
  is queued (single slot — newest replaces older). The agent picks it
  up automatically after the current turn finishes.
- **ESC anywhere**: cancels the running turn cleanly. The transcript
  shows a `✗ 已取消` marker; you stay at the input ready for the next
  prompt. (Ctrl+C does the same.)
- **Slash commands**: `/help /reset /tools /save <path> /quit` —
  identical to v1.5 simple mode.
- **Auto-confirmed costly tools**: `run_report` / `alpha_bench` no
  longer block on a `(y/N)` prompt; the transcript shows a
  `⚠ 启动耗时工具 — 按 ESC 随时取消` notice and proceeds. You can
  ESC out if you change your mind.

### Architecture

New module `financial_analyst.buddy.app`:
- `BuddyApp` class wraps a prompt_toolkit `Application` with
  `HSplit([transcript, conditional_spinner, input])` layout
- Rich → ANSI bridge: every transcript chunk goes through
  `_rich_to_ansi()` so existing `[bold]...[/]` markup still works
- Lazy `_build_application()`: state init is decoupled from terminal
  init, so tests can poke at state (submit/queue/cancel) without
  needing a real console
- Async animator task ticks the K-line spinner every 120 ms and calls
  `application.invalidate()` to schedule redraws
- ESC / Ctrl+C key bindings cancel `current_turn_task`; the task's
  finally block drains the queue and starts the next turn if any

### Migration

| Mode | Command | When |
|---|---|---|
| **v1.6 full TUI** (default) | `financial-analyst chat` | Default new UX |
| v1.5 simple REPL | `financial-analyst chat --simple` | If full TUI misbehaves |
| v0.x legacy slash | `financial-analyst chat --legacy` | Old slash-only TUI |

The `financial-analyst buddy` alias also now uses the v1.6 app.

### Tests

12 new tests in `tests/test_buddy_app.py`:
- Rich→ANSI bridge
- transcript growth on `_append_rich`
- banner present at startup
- submit-while-idle starts a turn
- submit-while-running queues (not starts)
- slash command handling (`/help`, `/reset`, `/tools`, `/save`)
- ESC cancels the current turn task
- queued input runs after current finishes

30 buddy tests pass total (11 agent + 7 animation + 12 app).

### Known limitations

- Tool confirmation modal: auto-accepts with an ESC-hint notice.
  Future: proper modal dialog.
- Mouse not enabled (`mouse_support=False`) since it conflicted with
  Windows terminal scroll. Use keyboard / PageUp / PageDown.
- The input field is single-line. Multi-line input via Shift+Enter is
  on the v1.6.x roadmap.

## v1.5.5 — 2026-05-20

### Added — ESC to cancel a running turn (Claude Code-style)

Pressing ESC during agent thinking / tool execution now cancels the
current turn cleanly and returns to the prompt. Ctrl+C does the same
thing.

```
❯ 跑一份 csi500 全因子 bench
⠋ 调用 alpha_bench…  [ESC 取消] ▇▃▄▇▂   -1.34%   #032
                                                       ← ESC pressed
✗ 已取消 (ESC). 继续输入下一个 prompt 或 /quit.

❯ 算了, 看下茅台行业就好          ← immediately type next prompt
⠋ 调用 industry_show…  ▆▃▅▇▂   +0.8%   #002
```

**Architecture**: `_run_turn_with_spinner` now spawns three concurrent
asyncio tasks per turn — the agent driver, the K-line animator, and
an ESC watcher (Windows uses `msvcrt.kbhit()` for non-blocking
keystroke polling at 60 ms cadence). `asyncio.wait(...,
FIRST_COMPLETED)` returns as soon as either the agent finishes or ESC
fires; on ESC we cancel the agent task, which propagates
`CancelledError` through the LLM call and tool dispatch chain.

Subprocess-based tools that started before ESC cannot be killed
mid-flight (subprocess.run is uninterruptible), but the result will
be discarded and the user is back at the prompt immediately. The
subprocess finishes in the background.

### Changed — spinner shows `[ESC 取消]` hint

The inline ticker line now reads:

```
⠋ 调用 chain_for…  [ESC 取消] ▇▃▄▇▂   -1.34%   #032
```

The hint is in `dim` style so it's permanently visible but doesn't
draw the eye.

Banner also mentions ESC + Ctrl+C as cancellation keys.

### Not done — input box visible during thinking (v1.6 planned)

Claude Code-style "you can keep typing while the agent thinks" still
requires a full prompt_toolkit `Application` rewrite — that's
substantial and slated for v1.6. For now, ESC gets you out instantly
and you can type the next prompt. Other keys pressed during a turn
are silently consumed.

## v1.5.4 — 2026-05-20

### Fixed — spinner freezes during long tools (event-loop block)

The K-line spinner animator ran on the asyncio event loop. When a tool
shelled out via `subprocess.run()` (run_report / alpha_bench /
mainline_radar / morning_brief / news_collect), the synchronous call
**blocked the entire event loop**, freezing both the spinner and the
agent's ability to process new events.

Symptom: bar counter (`#NNN`) stops incrementing in the middle of a
long tool, looks stuck even though the underlying subprocess is
working fine.

Fix: in `BuddyAgent.run_turn`, all tool calls now go through
`asyncio.to_thread(tool.run, **args)` so they execute on a worker
thread. The event loop stays free; the spinner keeps animating.

### Added — `news_collect` tool

The buddy registry was missing a way to refresh the news database, so
the LLM was forced to pick the wrong tool (e.g. `morning_brief`) when
users asked about 今日新闻 / 雪球情绪. New tool:

```python
news_collect(sources="kuaixun,longhu,sinafinance", limit=200, code=None)
```

Wraps `financial-analyst news-collect`. Supports public sources
(`kuaixun`, `longhu`, `sinafinance`, `shareholders`) and cookie-mode
sources (`xueqiu-comments`, `xueqiu-hot`, `xueqiu-earnings`).

### Changed — SYSTEM_PROMPT teaches the right news/sentiment flow

LLM now knows the correct chain when user asks about news/sentiment:

  1. `news_query` first to see what's cached
  2. If empty → `news_collect` to refresh the right sources
  3. `news_query` again to read freshly-collected data
  4. **Don't** use `morning_brief` for ad-hoc news (it's market-wide).

Tool registry: 13 → 14 tools. All 18 buddy tests still pass (no
behaviour change in tool-use loop logic — the to_thread wrap is
transparent).

## v1.5.3 — 2026-05-20

### Changed — K-line spinner compressed to inline sparkline

User feedback: the v1.5.1 6-row K-line block was too big and sat flush
against the previous text. v1.5.3 redesigns it as a single-line
sparkline ticker:

```
  ⠋ 整合中…  ▇▃▄▇▂   -3.12%   #000
  ⠙ 整合中…  ▄▄▇▃▂   -0.67%   #001
  ⠹ 整合中…  ▄▇▃▂▄   +1.47%   #002
  ⠸ 整合中…  ▇▃▂▄▂   -1.63%   #003
```

- **5 candles** (down from 18); each is one sparkline character
  (`▁▂▃▄▅▆▇█`) whose vertical fill encodes the close-price level within
  the visible window
- **1 row** of chart (down from 5), totalling **2 rows** with the
  blank padding above
- **Braille spinner** (`⠋⠙⠹⠸⠼⠴⠦⠧`) cycles every frame so motion is
  visible even when the sparkline hasn't shifted yet
- Up candles bright_green, down candles bright_red (unchanged)
- Live delta % + frame counter on the right
- One blank padding line above so the indicator doesn't slam against
  prior text

The animation is now ~30-40 columns wide, fits in a single visual
breath, and feels more "ticker-like" than the old chart-style block.

### Tests

7 animation tests updated to reflect the new 2-row layout (was 7-row).
All buddy tests (11 agent + 7 animation = 18) pass.

## v1.5.2 — 2026-05-20

### Fixed — config file not found in pip-installed wheels (HOTFIX)

`financial-analyst chat` crashed on first run from a fresh `.venv` with:

```
FileNotFoundError: 'G:\\...\\.venv\\Lib\\config\\llm.yaml'
```

**Root cause**: `LLMClient`, `loader_factory`, and `plugins` resolved
their config paths as `Path(__file__).parents[N] / "config" / *.yaml`,
which works in dev mode (repo root has `config/`) but breaks for
pip-installed wheels — the wheel never included the `config/` directory.

**Fix**: bundled all five config files into the package at
`financial_analyst/_resources/config/` and replaced the three
hard-coded paths with a shared `financial_analyst._config.find_config()`
lookup chain:

  1. Explicit `path=` argument
  2. `$FA_CONFIG_DIR/<name>` env override
  3. `~/.financial-analyst/config/<name>` user override
  4. `<cwd>/config/<name>` dev mode (repo root)
  5. Bundled `_resources/config/<name>` shipped default

Pip-installed users now Just Work without copying configs anywhere.
Dev-mode (`pip install -e .` from repo) still resolves the repo's
`config/` directory first, so live edits to `config/llm.yaml` take
effect immediately.

### Changed
- Wheel build now explicitly includes `src/financial_analyst/_resources/**/*`
  via hatch's `[tool.hatch.build.targets.wheel].include`. Confirmed via
  `zipfile.ZipFile.namelist()`: 5 yaml files present in the wheel.
- `buddy/repl.py` banner now reads `__version__` dynamically instead
  of hardcoding "v1.5.0".

### Migration note

If you previously copied `config/llm.yaml` to some custom location and
relied on the old `Path(__file__).parents[3]` lookup, that still works
via the cwd/config branch as long as you run the CLI from a directory
that has `./config/llm.yaml`. Cleaner: move your overrides to
`~/.financial-analyst/config/llm.yaml`.

## v1.5.1 — 2026-05-20

### Added — K-line thinking animation

The buddy REPL was silent during LLM thinking and tool execution.
v1.5.1 adds a finance-themed animated K-line chart that runs in a
Rich `Live` region at the bottom of the screen while the agent works:

```
█ █                                
█ █ ━ ━ ━ █                        
        │ █ ━ █   █ █              
              █ ━ █ █ ━ ━ █   █ ━ ━
                        │ █ ━ █ │  
  +1.59%   bar #005
  ▸ 调用 chain_for...
```

- 18-candle window, ~8 fps (one new candle every 120 ms)
- Bounded random walk with occasional shocks; up candles bright_green,
  down candles bright_red, doji as `━`
- Percentile-trimmed y-axis so a single big shock doesn't compress all
  other candles into doji-height
- Live status line: `思考中…` / `调用 <tool>…` / `整合中…`
- Live delta indicator: `+/-N.NN%   bar #NNN`
- Transient: clears when the agent finishes (no scrollback clutter)

**Architecture**: `KLineSpinner` (in `buddy/animation.py`) is a pure
state machine — `tick()` advances one bar, `render()` returns a Rich
`Group` of 5 chart rows + 1 delta row + 1 status row. The REPL wraps
the agent's turn in `rich.live.Live`, spawns an asyncio task that ticks
the spinner every 120 ms, and prints each `TurnEvent` ABOVE the live
region so the transcript scrolls normally while the spinner stays
pinned at the bottom.

### Refactored — repl.py

- Removed unused `_render()` (dead since the Live-region rewrite)
- Added `_run_turn_with_spinner()` and `_render_above_live()`
- Status transitions per event kind:
  - `text` → `思考中…`
  - `tool_call` → `调用 {tool}…`
  - `tool_result` → `整合中…`
  - `done` → exit

### Tests
- 7 new in `tests/test_buddy_animation.py`: init window size,
  group-renderable count, no-candles safety, tick continuity (new open
  == prior close), status persistence, status constants non-empty,
  red/green colouring on deterministic up/down candles.
- 18 tests total in `test_buddy*` (11 agent + 7 animation), all pass.

## v1.5.0 — 2026-05-20

### Added — Conversational front-end (Buddy)

A Claude Code-style conversational REPL: natural-language prompts in,
LLM autonomously picks tools, results stream back. Replaces the old
slash-command-only TUI as the default entry point for `chat`.

**Workflow**:
```
❯ financial-analyst chat
❯ 茅台是什么行业
[CALL] industry_show({'code': 'SH600519'})
[RESULT] SH600519: 白酒
贵州茅台（SH600519）属于白酒行业。需要我查看产业链或最新研报吗?

❯ 寒武纪在产业链什么位置 它有哪些同行
[CALL] chain_for({'code': 'SH688256'})
[RESULT] SH688256 → AI_chip_GPU (anchor, compute_chain upstream)
寒武纪（SH688256）核心产品: AI 加速 GPU/DCU
上游: 先进晶圆代工 / HBM 存储 / 先进封装
同行: 海光信息 / 景嘉微 / 国芯科技 / 紫光国微 / 芯原股份 / 龙芯中科
催化: NVDA GPU 发布周期 + BIS 出口管制 + 互联网云厂订单...
```

**13 tools auto-callable**:
- `run_report` (full deep-dive, confirm required)
- `quote_lookup`, `news_query`, `industry_show`
- `alpha_bench` (confirm required), `alpha_snapshot`, `alpha_list`, `alpha_show`
- `chain_for`, `stocks_show`
- `mainline_radar`, `morning_brief`, `dream_review`

Each tool's `description` is bilingual so the LLM matches Chinese
prompts. Costly tools (`run_report`, `alpha_bench`) gate behind a
confirmation callback — the REPL asks the user "(y/N)" before running.

**New module**: `financial_analyst.buddy/`
- `tools.py` — 13-tool registry with `Tool` dataclass, JSON schemas, run callable. Both Anthropic and OpenAI/Qwen function-call shapes via `to_anthropic_schema()` / `to_openai_schema()`.
- `agent.py` — `BuddyAgent` class with tool-use loop driven by LiteLLM. Yields `TurnEvent` (kind={text, tool_call, tool_result, error, done}) so the REPL can render as the agent thinks.
- `repl.py` — prompt_toolkit + Rich REPL. Slash commands: `/help /reset /quit /tools /save <path>`.

**Safety features**:
- Confirmation gate on costly tools (`run_report` ~5min, `alpha_bench` ~3min)
- `max_tool_iters=8` loop guard prevents infinite tool-call loops
- Tool errors surface verbatim to user + LLM (so it can recover)
- Conversation history persists across turns; `/reset` clears

**CLI**:
- `financial-analyst chat` — default → buddy
- `financial-analyst chat --legacy` — old slash-command TUI
- `financial-analyst buddy` — explicit alias

### Tests
- 11 new in `test_buddy.py`: registry sanity / schema well-formedness /
  confirm-required gating / single-turn text-only / single tool call /
  tool error recovery / declined confirmation / unknown tool name /
  max-iter loop guard / conversation state persistence.
- All 11 pass with mock LLM.
- Real LLM smoke tests (Qwen via DashScope) verify:
  - "茅台是什么行业" → industry_show → "白酒"
  - "寒武纪在产业链什么位置 它有哪些同行" → chain_for → upstream/peers/catalyst

### Migration note

The legacy slash-command TUI still ships (`chat --legacy`). New users
land on buddy automatically. No data migration needed — buddy stores
nothing persistent beyond `~/.financial-analyst/buddy_history.txt`
(prompt-toolkit input history).

## v1.4.6 — 2026-05-20

### Added — gtja143 + gtja149 (the last two "unportable" alphas)

Both alphas previously declared infeasible in v1.4.1 now ship,
bringing gtja191 to **191/191 (100%)** and alpha101 already at
101/101 — both reference catalogues complete.

**gtja143** was declared unportable because of its `SELF` recursion:
> `SELF_t = X_t * SELF_{t-1}` where `X_t = ratio` on up-days, `1.0`
> otherwise.

Closed-form realisation: cumulative product of the per-bar multiplier.
Per-code `cumprod` fits the stateless `compute(panel)` API without any
new "iterative" infrastructure. The handbook's `(CLOSE/DELAY)` form is
adopted (the literal `(CLOSE/DELAY-1)` decays to zero in tens of bars,
clearly a typo in some printings).

**gtja149** was declared unportable because of its benchmark
dependency:
> `REGBETA(FILTER(stock_ret, bench_close < delay(bench_close,1)),
>          FILTER(bench_ret, bench_close < delay(bench_close,1)),
>          252)`

New module `financial_analyst.data.loaders.benchmark.BenchmarkLoader`
fetches the chosen index close (CSI 300 default, configurable via
`FA_BENCHMARK` env var: `csi300 / csi500 / csi800 / csi1000 / zz500 /
sse / szse`), broadcasts to the panel index (same value across codes
per date). `PanelData.from_loader(..., benchmark_loader=...)` injects
the `benchmark_close` column.

New operator `filter_where(x, mask)` returns `x` where `mask` is True,
NaN elsewhere — natural way to express the GTJA `FILTER(...)`
construct. NaNs flow through `regbeta`'s rolling computation naturally.

`regbeta` gained an optional `min_periods` parameter (default `n`).
For filter-based alphas like gtja149 the rolling window is half NaN
by construction; we pass `min_periods=50` so the regression still
emits a beta with ~125 valid obs in the 252-bar window.

### Auto-loading

`alpha bench` and `alpha snapshot` now auto-detect a benchmark loader
just like industry: it's silently attached when the default loader can
fetch the index. If your loader can't serve `SH000300`, gtja149 just
returns NaN — bench result still completes for the other 441 alphas.

### Verified on synthetic 3-stock × 300-day panel

```
gtja143: last per code → 10.04 / 9.37 / 13.88  (cumulative up-day index)
gtja149: 561/900 non-null, betas in [-0.34, +0.34]
```

### Total catalogue status (final)

| Family | Covered | Of paper | Coverage |
|---|---:|---:|---:|
| alpha101 | 101 | 101 | **100%** |
| gtja191 | **191** | 191 | **100%** |
| qlib158 | 150 | 158 | 95% |
| **Total** | **442** | **452** | **98%** |

Compared to the Vibe-Trading 452-alpha reference target, the only
gap left is 8 of Qlib158's window-variant features (low signal value;
existing 150 cover all the underlying feature kinds).

### Tests
- +6 in `test_factor_zoo.py`: gtja143 cumprod reduction (hand-verified
  on 5-day sequence), gtja149 with-benchmark / without-benchmark
  branches, `filter_where`, `BenchmarkLoader.broadcast_to_panel_index`,
  env override, regbeta `min_periods` parameter. 28 zoo tests pass.

## v1.4.5 — 2026-05-20

### Added — Industry-chain knowledge base (chain_kb)

The last big knowledge-import gap from `G:\stocks` closes. Every report
on a stock that's in a known industry chain now sees: chain position,
upstream/downstream products, peer codes, role (anchor / data_supported /
llm_inferred) + weight, and the chain catalyst — fed directly into the
`fundamental-analyst` prompt.

**New module: `financial_analyst.data.loaders.chain_kb`**
- `ChainKBLoader` reads `~/.financial-analyst/memories/chain_kb/products/*.md`
  (override via `FA_CHAIN_KB_DIR`).
- Parses YAML frontmatter into `Product` dataclass: `node_id`,
  `display_name`, `category` (chain slug), `layer` (upstream/mid/down),
  `related_codes` (with role + weight), `upstream_products` /
  `downstream_products` graph edges, plus the markdown body for catalyst
  text.
- Builds a reverse code → products index on first load. Cached in memory;
  call `loader.reload()` to pick up changes.
- `chain_context(code)` returns a compact dict suitable for LLM injection:
  primary product (ranked by role-priority then weight), all products
  mentioning the code, upstream/downstream graph, top-N peer codes, and
  the "催化逻辑" tail of the primary product body.

**`factor-computer.chain_context`** (Dict, default empty): lookup
happens at report time. Silent skip if no chain file exists for the
code.

**`fundamental-analyst` prompt mandates**: when chain context is
present, must frame at least one bull/bear point around the chain
role, cite at least one peer code + the chain catalyst, and flag
`llm_inferred` chain links with weight < 0.5 as
`red_flags="chain_link_inferred_only"`.

### Added — `chain` CLI

```bash
financial-analyst chain list                            # 72 products across 6 chains
financial-analyst chain show AI_chip_GPU                # full content + frontmatter
financial-analyst chain for SH688256                    # which products + peers
financial-analyst chain import G:/stocks/strategy/chain_kb/products
financial-analyst chain stats
```

`import` filters: only files with `node_type: product` in the frontmatter
are copied; `_template.md`, `theme.md`, plain README's are skipped.

### Verified end-to-end

Bulk-imported 72 products × 6 chains × 158 unique stock codes from
`G:/stocks/strategy/chain_kb/products`. Example for SH688256 (寒武纪):

```
Stock SH688256 → primary product: AI_chip_GPU (AI 加速 GPU/DCU)
  Chain: compute_chain  layer=upstream
  Role: anchor weight=+1.00
  Upstream:   ['wafer_foundry_advanced', 'HBM_storage', 'advanced_packaging']
  Downstream: ['AI_server']
  Peer codes: 海光信息 / 景嘉微 / 国芯科技 / 紫光国微 / 芯原股份 / 龙芯中科
  Catalyst: NVDA GPU 发布周期 + BIS 出口管制 + 互联网云厂订单 ~10 万卡
```

### Knowledge-import status now (final)

| Source | Local destination | Status |
|---|---|---|
| `rating_system.md / pitfalls.md / factor_insights.md` | per-agent `memories/` | ✅ v0.x |
| `playbook_V1_V10 / R7-R20 / hard_rules` | per-agent `memories/` | ✅ v1.x |
| `stocks/{CODE}.md` (187 stocks) | `memories/stocks/<CODE>.md` | ✅ v1.4.4 |
| **`chain_kb/products/*.md` (72 products)** | `memories/chain_kb/products/` | ✅ **v1.4.5** |

All "经验 → 产出接通点" items from G:\stocks CLAUDE.md are now wired
into the financial-analyst report pipeline.

### Tests
- 13 new chain_kb tests: default path / env override / parsing / multi-product
  membership / anchor-rank priority / peer filtering / catalyst extraction /
  unknown code / list categories / import filters / reload / stats.

## v1.4.4 — 2026-05-19

### Added — Per-stock research timeline injection

The biggest knowledge-import gap from `G:\stocks` is closed. Each
stock now gets its accumulated research timeline injected into every
new report on that code, so the Bull / Bear / Risk-officer / Report-
writer agents see prior judgements, prior ratings, prior mistakes,
and explicit lessons — instead of starting cold every time.

**New module: `financial_analyst.data.loaders.stock_timeline`**
- `StockTimelineLoader` — reads `~/.financial-analyst/memories/stocks/<CODE>.md`.
- Override path via `FA_STOCK_TIMELINE_DIR` env var or ctor arg.
- API: `load(code)`, `load_tail(code, max_chars=4000)`, `list_codes()`,
  `import_from(source_dir, overwrite=False)`, `stats()`.
- Tail-mode loading caps at ~4 KB per stock to keep prompts bounded
  even when timelines reach 50 KB+.

**`factor-computer` now emits `stock_timeline`** (silent skip if no
file). The field carries the tail of the user's research markdown for
this code.

**Tier-3 agents now mandated to use it**:
- `bull-advocate`: SYSTEM_PROMPT requires citing prior rating + date
  and noting what's changed. User-message gets a `# 上次研报时间线
  (必读)` block.
- `bear-advocate`: same discipline; bear case must reconcile with
  prior judgements.
- `risk-officer`: SYSTEM_PROMPT extended to use the timeline
  specifically to catch **repeating prior mistakes** — if a trigger
  matches a previously-wrong call, emit
  `anti_signals="timeline_lesson_ignored:<reason>"`.
- `report-writer`: SYSTEM_PROMPT now requires a "上次回顾" section
  at the top of §一 综合评级 in `markdown_body`. The stock_timeline
  is stripped from the JSON dump and surfaced as its own block so
  the markdown body can cite the prior call directly.

### Added — `stocks` CLI

```bash
financial-analyst stocks list                                    # what's loaded
financial-analyst stocks show SH600100 [--tail 4000]             # show timeline
financial-analyst stocks import G:/stocks/strategy/stocks        # bulk copy
financial-analyst stocks import G:/stocks/strategy/stocks --overwrite
financial-analyst stocks stats                                   # n_codes + sizes
```

`import` filters non-stock files (skips `INDEX.md`, `missed_bulls_*.md`,
etc — only copies files whose stem matches `^(SH|SZ|BJ)\d+$`).

### Verified

```
$ financial-analyst stocks import G:/stocks/strategy/stocks
Imported 187 new stock timelines from G:/stocks/strategy/stocks
Total now: 187 codes
```

187 stocks × ~1.2 KB each = ~236 KB of accumulated research now
reachable by every relevant report. Each report on a code with a
timeline sees `<= 4 KB` of the latest entries in its Bull / Bear /
Risk / Report-writer prompts.

### Tests
- 10 new in `tests/test_stock_timeline.py` (default path /
  env-override / has-load-tail / short-file-no-truncation / unknown
  code / list-sorted / import-from / import-overwrite / missing-src /
  stats).

### Why this matters

CLAUDE.md (G:\stocks project) explicitly says:
> 经验 → 产出接通点: report_v2.py 生成研报时自动把 pitfalls /
> factor_insights / rating_system / stocks/{CODE}.md 的上次时间线
> 塞进 _agent_ctx/{CODE}.json 的 knowledge_pack 字段, agent_prompts.py
> 强制 sub-agent 必读.

The first three (`pitfalls / factor_insights / rating_system`) have
been in agent memories since v0.1. The per-stock timeline was the
missing piece — different per code, can't be embedded in a single
per-agent memory file. v1.4.4 closes it via factor-computer
injection + prompt mandates.

## v1.4.3 — 2026-05-19

### Added — `dream review / accept / reject` subcommands

The dream loop was code-complete since v0.3 but missing the
human-in-the-loop tools for triaging proposals. v1.4.3 closes that:

```bash
financial-analyst dream                                        # = dream run (default)
financial-analyst dream review                                 # list pending proposals
financial-analyst dream accept whale-analyst/no-vr-without-obv # promote to permanent
financial-analyst dream reject whale-analyst/bad-idea          # discard
```

- `dream review` walks `memories/_proposed/` and prints each proposal
  with `[confidence] agent/slug  (N cases)` + the title + the file path.
- `dream accept <agent>/<slug>` moves the proposal from
  `memories/_proposed/<agent>/<date>_<slug>.md` to
  `memories/<agent>/<slug>.md` (preserving the YAML frontmatter +
  body). Refuses to overwrite an existing permanent memory file.
- `dream reject <agent>/<slug>` deletes the proposal.

After accept, the next `financial-analyst report` call automatically
uses the new rule — markdown memory is hot-reloadable.

### Closing the self-update loop

End-to-end workflow now possible without leaving the CLI:

```bash
financial-analyst report SH600519                              # 1. run reports over time
# ...wait T+5d for outcomes...
financial-analyst dream                                        # 2. introspect
financial-analyst dream review                                 # 3. read what was proposed
financial-analyst dream accept whale-analyst/<slug>            # 4. promote good ideas
financial-analyst dream reject <other-agent>/<slug>            # 5. discard noise
financial-analyst report SH600519                              # 6. new rule in effect
```

### Tests
- 7 new dream CLI tests (review empty / review lists / accept promotes /
  accept refuses overwrite / reject deletes / accept unknown / accept bad
  target). 11 dream tests pass total; old 4 unchanged.
- Backward compat: `financial-analyst dream` with no args still defaults to `dream run`.

## v1.4.2 — 2026-05-19

### Added — dynamic zoo signal selection (440-rolling instead of fixed top-10)

The hardcoded `PRODUCTION_TOP10` is no longer the only path. v1.4.2 wires
up a rolling top-N pick from the latest bench result, so when the alpha
catalogue or universe regime shifts, the report pipeline picks up the new
strongest signals automatically.

**New module: `financial_analyst.factors.zoo.selector`**
- `select_top_alphas(bench_df, n=20, min_n_dates=30, min_abs_rank_ir=0.05,
   require_sign_agreement=True, family=None)` — filters out noise alphas
  (short bench, weak signal, sign-disagreement between `rank_IR` and
  `hit_rate`), then returns top-N by `|rank_IR|`.
- `load_latest_bench(universe)` — reads the canonical cached CSV.
- `alpha_metadata_from_bench(bench_df, names)` — returns
  `{name: {bench_rank_ic, bench_hit_rate, bench_n_dates}}` for snapshot
  enrichment.
- `bench_csv_path(universe)` — canonical filename for cached bench output.

**CLI: `alpha bench --save`**
- After benching, persists the full result CSV to
  `~/.financial-analyst/cache/bench_<universe>_latest.csv`. Used as the
  input for `snapshot --auto`.

**CLI: `alpha snapshot auto`**
- New target keyword: pass `auto` instead of `top10` or a comma-list.
- Reads the cached bench for the same `--universe`, picks the top-N
  (via `--top-n`, default 20) using `select_top_alphas`, builds the
  snapshot. Each row now carries `bench_rank_ic`, `bench_hit_rate`,
  and `bench_n_dates` so downstream LLM consumers know each alpha's
  validated direction without hard-coded sign conventions.

**Recommended workflow (weekly cron)**:
```bash
financial-analyst alpha bench --universe csi300_active \
    --since 2024-06-01 --until 2024-12-31 --save
financial-analyst alpha snapshot auto --universe csi300_active \
    --until 2024-12-31 --top-n 20
```

### Changed — `quant-analyst` SYSTEM_PROMPT is now sign-agnostic

Previously the prompt listed the v1.3.4 sign convention for the
hardcoded top-10 alphas (`qlib_VSTD60 POSITIVE`, `gtja095 NEGATIVE`,
etc.). With dynamic top-N this is no longer maintainable.

The new prompt teaches the LLM to derive direction per-alpha from each
row's `bench_rank_ic` sign:
- bullish if `(rank_pct > 0.7 AND bench_rank_ic > 0)` OR
  `(rank_pct < 0.3 AND bench_rank_ic < 0)`
- bearish symmetrically
- low-confidence if `|bench_rank_ic| < 0.05 OR bench_n_dates < 30`
- `bull_points` / `bear_points` must cite both `rank_pct` and
  `bench_rank_ic` so readers can verify the direction.

### Verified end-to-end on CSI300 / 2024-12-31

```
bench --save: 440 alphas across 868 codes × 144 days → CSV cached
snapshot auto --top-n 20: picked top-20 by |rank_IR|, each with
  bench_rank_ic + bench_hit_rate metadata. Examples:
    gtja042       bench_rank_ic=+0.0650  hit=52.5%
    qlib_VSUMP20  bench_rank_ic=-0.0457  hit=49.1%
    qlib_STD5     bench_rank_ic=-0.0701  hit=48.9%
    alpha089      bench_rank_ic=-0.0266  hit=48.9%
17219 rows = 20 alphas × ~860 codes
```

### Backward compatibility

- Old `snapshot top10` keyword still works → uses `PRODUCTION_TOP10`.
- Old snapshot parquet files (without bench metadata) still readable —
  rows just lack the optional `bench_*` columns and quant-analyst
  treats them as low-confidence.

## v1.4.1 — 2026-05-19

### Zoo catalogue completion — 440 alphas total

The closing batch toward complete coverage of the three reference
catalogues.

- **alpha101 +3 → 101/101 (100% COMPLETE)**. The final three:
  - `alpha056` — uses `cap` (market cap) in the original; we substitute
    `amount` (close × volume) since the formula only consumes `cap`
    inside `rank()`, where the ordering of dollar volume vs market cap
    is identical for cross-sectional ranking on A-share large caps.
  - `alpha071` — max of two decayed ts-ranks (close-ADV180 corr vs
    squared low+open-2*vwap rank). Long-window, ported as written.
  - `alpha073` — negative max of VWAP-momentum decay vs blend-delta
    decay ts-rank.
- **gtja191 +31 → 189/191 (99% COMPLETE)**. Added 112 (RSI direction),
  115 (high-close blend × ADV30), 121 (VWAP-floor × ADV60 ts-rank),
  123/148 (boolean corr-vs-floor), 124/125 (close-VWAP / decay
  composites), 131 (VWAP-delta × close-ADV50), 137 (single-day TR-
  normalised momentum), 138/140 (sister of alpha097/088),
  141 (high-ADV15 rank-corr), 146 (Z-score-style return deviation),
  152 (MACD on momentum), 154 (VWAP-floor boolean), 156 (sister to
  alpha073), 157 (deep-nested rank composite), 159 (triple-window
  stochastic %K composite), 162 (stochastic-RSI), 164 (smoothed
  up-day-inverse-return), 165 (cumulative-deviation range / 48d
  stddev), 166 (skewness-style central moment), 169 (MACD chain on
  EWMA momentum), 170 (sister to alpha047), 173 (TEMA + log
  correction), 180 (sister to alpha007), 181 (20d variance), 182
  (bench-aligned up-day proxy), 183 (cumulative-deviation excursion),
  187 (sister to gtja093), 190 (asymmetric vol log-ratio).
  Skipped permanently: 143 (recursive SELF — needs prior-step output;
  fundamentally incompatible with our stateless compute API), 149
  (benchmark-relative beta — requires benchmark return series we don't
  carry in PanelData).
- **qlib158 +23 → 150 (95% of 158 target)**. Wider window coverage for
  IMAX/IMIN/IMXD × {30,60}, SKEW/KURT × 5, CORR × 3, SUMP/SUMN/SUMD ×
  {10,30}, VSUMP/VSUMN/VSUMD × {10,30}, CNTD × {10,30}.

Total: **440 alphas across 3 families** — **97% of Vibe-Trading's
452-alpha reference target**. Compared to v1.3.0's 22 alphas, this is
a 20× expansion in two days.

### Fixed
- `gtja157` (nested ranks with `product()`) silently compute_error'd —
  `product` wasn't in gtja191's import list. Same class of bug as
  `alpha029` in v1.3.5. Fixed.

### Tests
- Count baselines bumped (alpha101 ≥ 101, gtja191 ≥ 189, qlib158 ≥ 150).
- All 18 zoo tests pass. Sample30 bench across 440 alphas runs to
  completion with 0 compute errors.

### What's truly unportable
Only 2 of the 452 reference alphas remain unportable, and they're both
architectural rather than complexity-bound:
- `gtja143`: recursive — formula references its own prior output as
  `SELF`. Our stateless `compute(panel) → series` API can't express
  this without major restructuring. Future work: optional iterative
  alphas (compute_iterative).
- `gtja149`: benchmark-index relative beta — needs the daily close of
  CSI 300 (or equivalent) as a parallel series in PanelData. Future
  work: BenchmarkLoader.

## v1.4.0 — 2026-05-19

### Added — Industry classifier loader + IndNeutralize alphas
v1.4.0 is the third pillar of the zoo: industry-neutralisation.
Previously stubbed, now wired end-to-end.

**`financial_analyst.data.loaders.industry.IndustryLoader`**:
- Pulls 申万 (Shenwan) level-1 industry classifications from Tushare
  `stock_basic(fields='ts_code,name,industry')` via the raw POST endpoint
  (bypassing the official `tushare` package's flaky round-robin).
- Caches to `~/.financial-analyst/cache/industry_map.parquet`. One
  refresh covers ~5500 A-share codes across ~110 industries.
- API: `get(code)`, `get_map(codes)`, `refresh_from_tushare()`, `stats()`.
- New CLI: `financial-analyst industry refresh / show / stats`.

**`PanelData.from_loader(..., industry_loader=...)`**:
- Optional kwarg. When provided, the panel carries an `industry` column
  indexed by (date, code).
- `panel.industry` property exposes the Series; falls back to `"未知"`
  when no loader is attached so old alphas don't crash.

**`indneutralize(x, group)` operator now actually used**:
- Already shipped as a stub in v1.3.0. v1.4.0 finally has data to feed
  it: alpha101 IndNeutralize alphas pass `panel.industry`.
- Verified: within any (date, industry) group, demean produces ~0 mean
  to floating-point precision.

**Alpha bench and snapshot CLI auto-load IndustryLoader** when the
cache exists (silent skip when absent).

### Added — alpha101 +19 → 98/101 (97%)
Final batch of IndClass-dependent alphas now operable:
- 048 (250d delta-corr, industry-demean)
- 058, 059 (IndNeutralize VWAP × volume corr)
- 063 (industry-neutral close-momentum vs blend-ADV180)
- 067 (IndNeutralize VWAP-ADV20 corr exponent)
- 069 (IndNeutralize VWAP-delta × blend-ADV20)
- 070 (IndNeutralize close × ADV50 long-corr)
- 076 (IndNeutralize low × ADV81 multi-decay)
- 079 (IndNeutralize blend-delta vs VWAP-ADV150)
- 080 (IndNeutralize open-high blend sign-delta)
- 082 (IndNeutralize volume × open corr)
- 087 (IndNeutralize ADV81 × close corr decay)
- 089 (IndNeutralize VWAP-delta vs low-ADV10 ts-rank)
- 090 (IndNeutralize ADV40-low corr)
- 091 (IndNeutralize close × volume long-decay)
- 093 (IndNeutralize VWAP × ADV81 corr)
- 095 (boolean composite on long-window corr)
- 097 (IndNeutralize blend-delta vs low-ADV60 ts-rank)
- 100 (IndNeutralize MFI-volume composite)

Zoo: **383 alphas** across 3 families.

### Real-world signal on sample30
14 of 19 new IndNeutralize alphas produce real signals (5 need >144
trading days to warm up due to 250d / adv150 / adv180 windows):

```
alpha089  rank_IR=-0.324  (industry-neutral VWAP-delta vs low-ADV10)
alpha091  rank_IR=-0.194  (IndNeutralize close × vol long-decay)
alpha067  rank_IR=+0.171  (IndNeutralize VWAP-ADV20 corr)
alpha069  rank_IR=-0.067  (IndNeutralize VWAP × ADV20 blend)
alpha080  rank_IR=-0.100  (IndNeutralize open-high blend)
```

### Tests
- New: `test_indneutralize_demean_per_industry` — verifies the
  per-(date, industry) demean invariant on hand-built groups.
- New: `test_industry_loader_round_trip` — IndustryLoader cache I/O
  without touching Tushare.
- New: `test_panel_carries_industry_when_loader_supplied` — wiring test
  for the new `industry_loader` kwarg.
- 18 zoo tests pass total.
- Baselines bumped (alpha101 ≥ 98).

### Remaining (v1.4.x+)
- alpha101: 3 left — 033 (?), 056 (uses cap = market cap), 071/073
  (very complex nested ts-rank chains).
- gtja191: 33 left (recursive SELF, benchmark-relative, exotic SUMIF).
- qlib158: 31 left (low-marginal-value window variants).

### Upgrade note
After upgrading, run:
```bash
financial-analyst industry refresh
```
once to populate the industry cache. From then on, every `alpha bench`
and `alpha snapshot` call automatically uses it.

## v1.3.6 — 2026-05-19

### Added — +74 alphas (zoo: 290 → 364)
Final pre-IndustryLoader push toward Vibe-Trading parity.

- **gtja191 +49 → 158/191 (83%)**: added 064, 073, 075, 087, 089, 090,
  091, 092, 094, 101, 103-105, 107, 108, 110, 111, 113, 114, 116, 117,
  119, 120, 122, 127, 130, 132, 134, 136, 142, 144, 145, 147, 151, 153,
  155, 158, 163, 171, 172 (ADX-style), 174, 175 (short ATR), 177, 179,
  185, 186, 188, 189, 191. Hits MACD-style (089/155), Williams %R
  variants, multi-window OBV, recency indicators, CCI/ADX patterns,
  short ATR, rolling kurtosis-style displacement (127).

- **qlib158 +25 → 127/158 (80%)**: rolling SKEW/KURT × {10,20,60} (6),
  MA30/STD30/ROC30 (3), longer CORR/CORD × {10,60} (4),
  WVMA × {10,30} (2), VMA/VSTD × {10,30} (4), CNTP/CNTN × {10,30} (4),
  RSV × {30,60} (2).

**Zoo: 364 alphas across 3 families** —
80%+ of two of the three reference catalogues.

### Remaining work (v1.4.0 +)
- alpha101: 22 left, all use `IndNeutralize` (need IndustryLoader). v1.4.0.
- gtja191: 33 left, mostly very complex/exotic (recursive `SELF`,
  benchmark-relative formulas, multi-stage SUMIF). Incremental.
- qlib158: 31 left, mostly window variants of existing features that
  add no signal capacity. Optional.

### Tests
- 15 zoo tests pass; baselines bumped (alpha101 ≥ 79, gtja191 ≥ 158,
  qlib158 ≥ 127).
- Sample30 bench across 364 alphas completes with 0 compute errors.

## v1.3.5 — 2026-05-19

### Added — +148 alphas (zoo: 142 → 290)
A push toward catalog completeness. Three batches across three families:

- **alpha101 +37 → 79/101** (77% of WorldQuant's catalogue): added
  021, 027, 029, 031, 032, 036-039, 046, 047, 051, 057, 060-062, 064-066,
  068, 072, 074, 075, 077, 078, 081, 083-086, 088, 092, 094, 096, 098,
  099, 101. Skipped: ~22 that need `IndNeutralize` (industry classifier
  loader, planned for v1.4.0) and a few using `cap` (market cap from
  daily_basic, not yet in PanelData).
- **gtja191 +65 → 109/191** (57% of GTJA's catalogue): added 015, 016,
  023, 026, 030, 032, 033, 035, 036, 039, 041, 043-045, 048-051, 055, 056,
  059-063, 066, 067, 069-072, 074, 077-086, 088, 093, 096-100, 102, 106,
  109, 118, 126, 129, 133, 135, 139, 150, 161, 167, 168, 176, 178, 184.
- **qlib158 +46 → 102/158** (65% of Qlib's Alpha158): new
  SUMP/SUMN/SUMD × {5,20,60} on close (9), VSUMN/VSUMD × {5,20,60} (6),
  CORD × {5,20} (2), WVMA × {5,20,60} (3), MAX/MIN × 4 windows (8),
  QTLU/QTLD × 4 windows (8), RANK × 4 (4), CNTD × 3 (3),
  IMXD × 3 (3).

**Zoo now ships 290 alphas total** — close to two-thirds of the original
Vibe-Trading 452-alpha goal. Remaining: ~80 alpha101 (mostly
IndNeutralize-blocked), ~82 gtja191 (mostly complex/exotic), ~56 qlib158.

### Fixed
- `alpha029` / `alpha081` used `product()` but it wasn't imported into
  alpha101/alphas.py — silent `compute_error` until now. Fixed.
- `qlib_CORD5` / `qlib_CORD20` used `log()` for log-volume ratios but
  it wasn't imported into qlib158/alphas.py — same silent error. Fixed.

### Tests
- 15 zoo tests still pass; count baselines bumped (alpha101 ≥ 79,
  gtja191 ≥ 109, qlib158 ≥ 102).

### Performance note
A `alpha bench --universe csi300_active` over 290 alphas now takes
~3-4 minutes (vs ~2m for 142 in v1.3.3). All alphas use the same
panel — adding more alphas grows linearly in benchmark time.

## v1.3.4 — 2026-05-19

### Added — Alpha-Zoo snapshot integration into the research pipeline
The 142-alpha zoo finally reaches end users. New flow:

1. **Periodic snapshot** — user runs
   `financial-analyst alpha snapshot top10 --universe csi300_active --until 2024-12-31`
   weekly (or any cadence). Output:
   `~/.financial-analyst/cache/zoo_snapshot_<universe>_<asof>.parquet`,
   with one row per (code, alpha) carrying the current value plus the
   stock's cross-sectional percentile rank within the snapshot universe.
2. **Factor-computer auto-lookup** — every stock report now looks up
   the most-recent snapshot whose asof ≤ report asof and surfaces a
   `zoo_signals` block with the target stock's values + rank_pct for the
   curated production-top-10 alphas. Silent skip when the cache is
   absent (preserves backward compatibility).
3. **Quant-analyst consumes** — `quant-analyst`'s system prompt now
   includes the v1.3.4 sign conventions (positive vs negative-rank
   alphas) and decision rules:
   - 3+ zoo alphas agreeing with the model bumps conviction
   - Zoo + model disagreement flagged as `zoo_model_disagreement` in
     `anti_signals`
   - `bull_points` must cite specific zoo alphas by name + rank_pct

### Added — `PRODUCTION_TOP10` curated alpha list
Hard-coded in `financial_analyst.factors.zoo.snapshot`. Derived from the
CSI300 2024-H2 bench (docs/csi300_bench_2024h2.md §8) — the 10 alphas
with strongest cross-universe `|rank_IR|` and >50% hit rate:

```
qlib_VSTD60, gtja095, qlib_STD10, gtja052, gtja042,
qlib_VSUMP20, qlib_KLEN, qlib_BETA20, qlib_ROC60, qlib_IMAX20
```

### Tests
- `test_snapshot_round_trip` — builds a 40-stock snapshot with a stub
  loader and verifies `load_snapshot_for_code` round-trips correctly.
- Full test suite: 15 zoo tests pass.

### Verified end-to-end on SH600519 (asof 2024-12-31)
Snapshot lookup shows the LLM:
```
qlib_VSTD60   rank_pct=19.8%   (low — bearish for VSTD60 positive sign)
gtja095       rank_pct=98.4%   (high turnover vol — bearish, negative sign)
qlib_STD10    rank_pct=10.9%   (low close vol — bullish, negative sign)
gtja042       rank_pct=8.9%    (low vol-of-high crowd — bearish, positive sign)
qlib_KLEN     rank_pct=2.1%    (very tight range — bullish, negative sign)
qlib_BETA20   rank_pct=74.4%   (strong 20d slope — bearish, negative sign)
qlib_ROC60    rank_pct=67.8%   (mid-high 60d ratio — slightly bullish)
```

quant-analyst now produces `bull_points` / `bear_points` grounded in
these specific alpha readings instead of just the LGB rank.

### Roadmap
- v1.3.5: industry-neutralise the volatility alphas (need industry
  classifier loader first — Tushare `stock_basic` has `industry` field)
- v1.3.x: backfill remaining alpha101/gtja191/qlib158 alphas (~310 left)
  for completeness, even though the top-10 already captures 80%+ of
  bench signal magnitude.

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
