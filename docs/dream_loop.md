# Dream Loop — agent self-improving memory

`/dream` (or `financial-analyst dream`) runs an introspection pass over past reports:

1. Scan `out/*.json` for reports asof within `--since N` days
2. For each, fetch T+5d / T+20d actual prices via the configured loader
3. Score each prediction → verdict ∈ {correct, wrong, partial, pending}
4. LLM-driven `Introspector` sub-agent reads wrong/partial cases + current memories
5. Proposes memory updates → written to `memories/_proposed/<agent>/<date>_<slug>.md`
6. You review with `/memory list-proposals`, then `accept` or `reject`

## Workflow

```bash
# Build up history first
financial-analyst report SH600519
financial-analyst report SZ000858
# ... wait 5+ trading days for T+5d outcomes to materialize ...

# Introspect
financial-analyst dream --since 30

# Output:
# Found 14 reports in last 30 days
#   verdicts: {'correct': 8, 'wrong': 3, 'partial': 2, 'pending': 1}
# Introspecting 5 wrong/partial cases (via LLM)...
# Generated 2 proposals:
#   [med] bull-advocate/vol-neutral-bull-bias: RSI mean-reversion fails in neutral vol
#   [low] risk-officer/high-pe-mid-cap: PE>80 mid-caps still risky despite mv>200亿
# Wrote 2 proposals to memories/_proposed/

# Review in TUI
financial-analyst
> /memory list-proposals
> /memory show _proposed/bull-advocate/2026-05-18_vol-neutral-bull-bias.md
> /memory accept _proposed/bull-advocate/2026-05-18_vol-neutral-bull-bias.md
> /memory reject _proposed/risk-officer/2026-05-18_high-pe-mid-cap.md
```

## Verdict rules

| Action predicted | Correct when | Wrong when |
|---|---|---|
| buy | T+5d return > 2% OR target hit | stop_loss hit OR T+5d < -2% |
| hold | -2% ≤ T+5d ≤ 2% | otherwise (partial) |
| sell | T+5d return < 0 | T+5d return > 0 |
| avoid | T+5d return ≤ 0 | T+5d return > 0 (partial) |

`stop_loss` hit (T+1..T+5 low ≤ stop_loss) always = wrong.

## Confidence levels

Introspector emits low / med / high based on supporting case count:
- 2 cases → low
- 3-5 → med
- 6+ → high

**Auto-accept is not implemented** — all proposals require human review. For quant systems, an incorrect memory update can compound losses.

## Safety

- Proposals are markdown with YAML frontmatter (auditable)
- `/memory reject` permanently deletes a proposal
- `/memory accept` moves the file from `memories/_proposed/<agent>/` to `memories/<agent>/`
- After accept, the FTS5 memory index re-indexes on next TUI startup (or run `/memory reindex`)
- Sub-agent prompt picks up the new file on next invocation (memory load is per-call)

## Limits

- v0.3 introspector targets one agent per proposal
- Proposals do not currently get auto-merged into existing memory files — they live alongside as separate files
- The LLM driving introspection itself has biases; treat proposals as drafts, not directives
