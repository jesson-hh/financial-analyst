# Ask Command — Natural-Language Front Desk

`financial-analyst ask "<query>"` (or `/ask <query>` in TUI) is a lightweight natural-language entry that uses tool-calling to answer your question without running the full 13-agent deep-dive.

## Use cases

```bash
financial-analyst ask "我最近的研报里 bear 段是不是过激了"
financial-analyst ask "SH600519 现在 PE 多少 / 最新报告说什么"
financial-analyst ask "memories 里关于游资博弈票的规则是什么"
financial-analyst ask "最近 dream 提议了哪些新规则"
financial-analyst ask "给我完整看一下 SZ002594"   # → 建议你跑 /report SZ002594
```

## How it works

1. ONE LLM call (with tool-calling) decides which of 6 tools to call:
   - `list_past_reports()` — recent reports in `out/`
   - `read_past_report(code, date?)` — markdown of a specific report
   - `search_memory(query, agent?)` — FTS5 across all agent memories
   - `quick_quote(code)` — latest OHLCV + PE/PB/MV (no LLM, fast)
   - `quick_factors(code)` — 34 daily factors (no LLM, fast)
   - `list_dream_proposals()` — staged dream proposals

2. Tool calls run (cheap, no LLM inside).
3. ONE more LLM call synthesizes the markdown answer.

Total: 2 LLM calls + N cheap tool calls. Typical latency ~10-30s vs ~10 min for full deep-dive.

## When ask command escalates to full report

If your question is "give me your full analysis on X" or similar, ask agent returns `needs_full_report=true` and `suggested_code=X`. The CLI prints a hint:

```
This requires a full deep-dive. Run:
  financial-analyst report SH600999
```

You decide whether to run the slow path.

## Tool schemas

All 6 tools follow OpenAI-compatible function-call JSON schema. See `src/financial_analyst/ask/tools.py:TOOL_SCHEMAS`.
