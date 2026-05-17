# Writing Memory Files

Memory files are markdown documents in `memories/<agent-name>/` that get appended to a sub-agent's system prompt at runtime.

## Principles

- **Memory holds experience, code holds workflow.** A rule that changes — put it in memory. A logic that does not — put it in code.
- **One concept per file.** Do not dump everything into one markdown.
- **Why before what.** Lead with the principle, then the rule, then examples.
- **Hot-reload friendly.** Edits take effect on the next agent invocation. No restart.

## Structure recommendations

```markdown
# <Topic>

## Why this rule exists
<context — past incident, validated research, etc.>

## The rule
<concrete actionable directive>

## How to apply
<when this fires, what to do>

## Examples
- positive case: ...
- negative case: ...
```

## Per-agent guidance

| Agent | What to put in memory |
|-------|----------------------|
| `fundamental-analyst` | Valuation methodology, market-cap tier rules, red-flag heuristics |
| `technical-analyst` | Factor IC/ICIR cheat sheet, MA/RSI/MACD interpretation rules |
| `whale-analyst` | R7-R20 sentiment signals, board scorer dimensions, whale-judge rules |
| `quant-analyst` | Model consensus rules, anti-signals, validated failure modes |
| `bull-advocate` | V1-V9 playbook anchors, long-side factor patterns |
| `bear-advocate` | F1-F14 failure modes, pitfalls library, valuation traps |
| `risk-officer` | Hard rules (cannot be overridden), V10 execution discipline, game-capital ticker veto |
| `report-writer` | Rating system, report template, V10 anchor |
| `news-reader` | Extraction rules (what to capture, what to skip) |
| `f10-reader` | Known game-capital whitelist, event classification rules |

## Shared memory

Files in `memories/_shared/` are read by ALL agents. Use for cross-cutting playbooks (e.g. V1-V10).

## Cross-agent borrowing

In a swarm yaml, declare `borrows_memory: [other-agent]` to grant read access:

```yaml
- name: risk-officer
  borrows_memory: [bear-advocate]   # CRO sees bear's pitfalls
```

## CLI commands

- `/memory list <agent>` — list memory files
- `/memory reload` — clear cached memory (next invocation reloads)
