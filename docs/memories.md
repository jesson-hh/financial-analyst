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

## Retrieval Mode (v0.2+)

For agents with large memory libraries, you can switch from full-inline injection to FTS5-retrieved top-K snippets. This drops the system-prompt token cost ~60% without losing the most relevant context.

In `config/swarm/<preset>.yaml`, mark an agent for retrieval:

```yaml
agents:
  - name: bear-advocate
    memory_mode: retrieval    # default: full
```

When `memory_mode: retrieval` is set AND the orchestrator builds the agent with a `MemoryIndex`, the agent calls `memory.load_relevant(query, top_k=5)` instead of `memory.load_all()`. The query is built from the agent's upstream JSON (first 1500 chars, FTS5-safe alphanumeric+CJK tokens). The `_shared/` directory is always included (core rules everyone needs).

The FTS5 index lives at `~/.financial-analyst/cache/memory.fts5.db` and rebuilds incrementally on each TUI startup (via file mtime tracking). You can force a rebuild with `/memory reindex`.

## CLI commands

| Command | Purpose |
|---------|---------|
| `/memory list <agent>` | List markdown files in `memories/<agent>/` |
| `/memory show <agent>/<file>` | Print markdown content (rendered) |
| `/memory edit <agent>/<file>` | Open in `$EDITOR` (notepad on Windows by default) |
| `/memory search <query>` | FTS5 full-text search across all memories (top 10) |
| `/memory stats` | Per-agent file count + byte size from FTS5 index |
| `/memory diff` | Last 7 days of `git log` restricted to `memories/` |
| `/memory reindex` | Force rebuild of FTS5 index from filesystem |
| `/memory reload` | Clear in-memory caches (next agent invocation re-reads) |

The `search` command auto-appends `*` to query tokens so prefix queries match (helpful for CJK terms like `游资*` matching `游资博弈`).
