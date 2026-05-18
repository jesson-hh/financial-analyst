# Architecture

## Three Trust Tiers

| Tier | Agents | Trust | Write? |
|------|--------|-------|--------|
| 1 (data) | quote-fetcher, factor-computer, model-predictor, news-reader, f10-reader | mixed (3 trusted, 2 untrusted) | No |
| 2 (analyst) | fundamental, technical, whale, quant | trusted | No |
| 3 (decision) | bull, bear, risk-officer, report-writer | trusted | Only writer |

## DAG

```
Orchestrator
  |- quote-fetcher  -|
  |- factor-computer  |
  |- model-predictor  |   Tier 1 (parallel, asyncio.gather)
  |- news-reader      |
  `- f10-reader ------+
        |
  |- fundamental-analyst -|
  |- technical-analyst    |  Tier 2 (parallel after Tier 1)
  |- whale-analyst        |
  `- quant-analyst -------+
        |
  |- bull-advocate --|
  `- bear-advocate --+  Tier 3a (parallel debate)
        |
   risk-officer        Tier 3b (CRO independent review)
        |
   report-writer       Tier 3c (sole writer)
```

## Security Model

- Untrusted text (news, F10) only enters `news-reader` and `f10-reader`
- These readers run with `tools=[read, grep]` only; output is pydantic-validated JSON with char-whitelist and length caps on string fields
- All other agents consume only structured JSON, never raw untrusted text
- Only `report-writer` has filesystem write access (`out/<code>_<date>.md` + `.json`)

## Memory Injection

Each agent loads its own `memories/<name>/*.md` + `memories/_shared/*.md` at instantiation. The concatenated content is appended to the agent's system prompt as a `# Memory` section. Hot reload by calling `agent.memory.reload()`.

**v0.2: retrieval mode.** Agents configured with `memory_mode: retrieval` in their swarm preset entry switch from `load_all()` to `load_relevant(query, top_k=5)`, backed by a shared SQLite FTS5 index over `memories/**/*.md`. The query is derived from the agent's upstream JSON. `_shared/` is always included regardless of mode.

## Extension Points

| Axis | Interface | Default v0.1 | How to extend |
|------|-----------|---------------|---------------|
| Data loader | `BaseLoader` | TushareLoader | Implement `BaseLoader`, register in `config/loaders.yaml` |
| Model | `BaseModel` | LGBMomentumModel | Implement `BaseModel`, register via `ModelRegistry.register()` |
| KB | `KnowledgeBase` | LocalMarkdownKB | Implement, inject into sub-agent |
| Sub-agent | `SubAgent` | 13 built-in | Implement, register, add to preset yaml |
| Swarm preset | YAML | stock-deep-dive | Drop new yaml into `config/swarm/` |
| Memory mode | per-agent `memory_mode` yaml key | full | Set `retrieval` to switch to FTS5 top-K retrieval (cuts prompt tokens) |
