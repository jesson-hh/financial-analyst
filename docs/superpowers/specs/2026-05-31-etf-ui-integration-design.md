# ETF UI Integration — Design Spec

**Date:** 2026-05-31
**Status:** Approved design (brainstorming) → pending implementation plan
**Scope:** financial-analyst 觀瀾 Web UI — make ETFs first-class in the chat + watchlist, the same way A-share stocks already are.

## Goal

Make ETFs accessible from the 觀瀾 Web UI exactly like A-share stocks:

- **(A) Chat** — typing an ETF code/name in the conversation routes (by intent, like stocks) to either a quick realtime-quote card ("现价/多少钱") or a full ETF deep-dive report ("分析/研报/怎么样").
- **(B) 盯盘** — ETFs can be added to the watchlist / monitoring wall for realtime price monitoring (price / change% / volume / amount), reusing the stock monitoring wall.

Design principle: **reuse the existing code-driven plumbing**; add only what is genuinely missing.

## Background / Current State (verified this session)

- The Web UI (`fa start` → backend `:9999` + UI `:5173`) drives everything through the buddy **LLM agent**: frontend `POST /run {query}` → `agent.run_turn(query)` → the LLM picks tools → a tool returns `side_effect`; the server emits SSE events. For reports it emits `("report", {"path": md_path})` for **any** tool whose result carries `side_effect["md_path"]`; the frontend renders that markdown.
- **buddy/ has ZERO ETF references** — the chat agent has no ETF tool and cannot produce ETF reports today.
- ETF analysis exists as **CLI only**: `fa etf-report <code>` → `run_etf_report_oneshot(code, asof, out_dir, trace)` (tui.py) runs the 13-agent `etf-deep-dive` swarm → writes `.md/.json/.html` to `out/` (same report_writer output shape as the stock report).
- Realtime quotes (`TencentQuoteCollector`, `qt.gtimg.cn`) **already cover ETFs** — empirically verified 2026-05-31: `SH510300`→沪深300ETF 4.923 / -0.18%, `SZ159915`, `SH588000`, `SH563300` all return live price/chg%/vol/amount. Same source powers chat `realtime_quote`/`realtime_quotes` tools and the `/quotes` monitoring-wall endpoint + watchlist add-resolve.
- **The gap = code normalization.** `normalize_code` (buddy/tools.py:42) and `_to_tencent` (data/collectors/tencent_quote.py:24) map bare 6-digit codes `6→SH / 0,3→SZ / 8,4→BJ` but have **no ETF rule** → bare ETF codes (`510300`, `159915`) fall through unprefixed → Tencent query / loaders fail. Prefixed (`SH510300`) and suffixed (`510300.SH`) ETF codes already work in both functions.

## Design (v1 — reuse existing plumbing)

### Shared fix — ETF-aware code normalization (A & B both depend on this)

Add ETF prefix rules to the bare-6-digit branch of **both** `normalize_code` and `_to_tencent`:

- `51` / `56` / `58` prefix → Shanghai (`SH` / `sh`). Covers 510–519 (incl. 511 bond-ETF, 518 gold), 560–563, **588** (STAR-board ETF).
- `15` prefix → Shenzhen (`SZ` / `sz`). Covers 159xxx / 150–159.
- Use **precise 2-char prefixes** (`51`/`56`/`58`/`15`), NOT broad `5→SH` / `1→SZ`, to avoid colliding with SH/SZ convertible bonds (11x/12x/13x). Bonds are out of scope.
- Extract a single shared helper `financial_analyst/data/code_norm.py :: etf_exchange(code6) -> 'SH'|'SZ'|None` used by both call sites, so the rule has **one** definition and one unit-test surface. (Both existing functions keep their signatures; they just consult the helper inside the bare-6-digit branch.)

**Outcome:** bare ETF codes resolve correctly across chat `realtime_quote`, the `/quotes` wall, watchlist add, and the loaders.

### A — ETF in chat (intent routing, mirrors stocks)

- **New buddy tool `run_etf_report(code, asof=None)`** in buddy/tools.py, mirroring `_tool_report`:
  - Calls `run_etf_report_oneshot(code=normalize_code(code), asof=asof, out_dir=<out_dir>, trace=...)` using the same invocation pattern `_tool_report` uses for `run_report_oneshot`.
  - Returns a `ToolResult` whose `side_effect = {"md_path": <generated .md path>}`.
  - `cost_hint="minutes"` + `confirm_required=True` (identical to `run_report` — the UI confirms before the heavy ~13-LLM-call run).
  - Registered in the tool registry next to `run_report`.
- **Report rendering is free**: the existing `/run` handler already emits `("report", {path})` SSE for any tool returning `md_path`; the frontend renders the ETF `.md` identically to a stock `.md` (same report_writer structure). No frontend report-renderer change.
- **Agent routing** (the LLM picks tools by description): give `run_etf_report` a clear description and add one line to the agent's tool-routing guidance — *"ETF 代码(5/15 开头, 如 510300 / 159915 / SH510300)深度研报用 `run_etf_report`; 实时价用 `realtime_quote`."* The LLM then routes "分析/研报/怎么样" → `run_etf_report`, "现价/多少钱" → `realtime_quote` (which now handles bare ETF codes via the shared fix).
- Stock tools are untouched; ETF uses its own tool. No conflation.

### B — ETF in 盯盘 (reuse the stock monitoring wall)

- After the shared fix, `GET /quotes?codes=510300,SH159915,…` and the watchlist add-resolve path return ETF quotes with **no further backend change**.
- **Frontend tweak** (the monitoring-wall component in the served UI dir — `src/financial_analyst/ui/` app, with the `packaging/src-tauri/ui/` mirror kept in sync): when a row's code is an ETF (5/15 prefix), **hide the PE / PB / 市值 columns** (meaningless for ETFs — Tencent returns 0); keep price / change% / volume / amount.
- v1 does **NOT** add 折溢价 / IOPV to the wall (needs the `etf_spot`/akshare path + its own refresh cadence; deferred).

## Error Handling

- Code normalization: unknown/invalid codes return as-given (existing behavior) → Tencent returns no quote → UI shows blank/—, no crash.
- `run_etf_report`: if the ETF code is unresolvable or the swarm fails, return a `ToolResult` error (mirror `_tool_report`'s error path); the chat surfaces the message, no SSE report event is emitted.
- ETF outside our `etf_*.parquet` universe: the report swarm already degrades gracefully on thin/missing fundamentals; the realtime quote still works via Tencent regardless of universe membership.

## Testing

- **Unit** — shared `etf_exchange` helper (and the two call sites): bare `510300`→SH, `159915`→SZ, `588000`→SH, `560000`→SH; prefixed/suffixed ETF still correct; **stock codes unchanged** (600519→SH, 000001/300750→SZ, 430017→BJ); **bonds not mis-mapped** (e.g. 110xxx / 123xxx stay as-given, not forced to an ETF exchange).
- **Unit** — `run_etf_report` tool returns `side_effect={"md_path": …}` (mock `run_etf_report_oneshot`).
- **Integration** — `/quotes?codes=510300,SH510300,159915` returns ETF rows (mock `TencentQuoteCollector.fetch`).
- **Manual smoke** — chat "分析 510300" → confirm → ETF report renders; "510300 现价" → quote card; add 510300 to 盯盘 → live price with PE/PB/市值 hidden.

## Out of Scope (v1)

- 折溢价 / IOPV in the monitoring wall (deferred — needs the `etf_spot`/akshare path + refresh).
- ETF-specific report HTML rendering changes (reuse the stock renderer as-is).
- Batch `fa etf-report -f` from the UI.
- ETF name→code resolution in chat beyond what `TencentQuoteCollector.quote` already provides.

## Alternatives Considered

- **Merge ETF into the existing `run_report` tool** (detect ETF inside, dispatch to the etf swarm): rejected — conflates two swarms / report-writers in one tool, harder to test, muddier responsibility.
- **Frontend-side `/etf-run` endpoint** (frontend detects ETF, calls a dedicated endpoint): rejected — extra endpoint + frontend routing, bypasses the unified chat agent, doesn't match "跟个股一样".

## Touch Points (files)

| File | Change |
|------|--------|
| `src/financial_analyst/data/code_norm.py` (new) | shared `etf_exchange(code6) -> 'SH'\|'SZ'\|None` helper |
| `src/financial_analyst/data/collectors/tencent_quote.py` | `_to_tencent` consults `etf_exchange` in bare-6-digit branch |
| `src/financial_analyst/buddy/tools.py` | `normalize_code` consults `etf_exchange`; new `run_etf_report` tool + registration + agent routing description |
| `src/financial_analyst/ui/` app (+ `packaging/src-tauri/ui/` mirror) | hide PE/PB/市值 for ETF rows in the monitoring wall |
| `tests/` | unit (helper + tool) + integration (`/quotes`) tests above |

## Notes for the implementer

- Confirm `_tool_report`'s exact async-invocation pattern (how it runs the async `run_report_oneshot` from a sync tool `run`) and mirror it for `run_etf_report`.
- Confirm the served UI dir + the watchlist-wall component file before the frontend edit (the launcher serves `src/financial_analyst/ui/` by `_ui_dir()` priority; keep the `packaging/src-tauri/ui/` copy in sync, and bump the `index.html` `?v=` cache-buster per the project's JSX-cache convention).
- Editable install on this machine resolves to `G:/financial-analyst/src`; run tests with the fa `.venv` (`G:/financial-analyst/.venv/Scripts/python.exe -m pytest`).
