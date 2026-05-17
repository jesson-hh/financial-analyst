# Financial Analyst

A-share single-stock deep-dive multi-agent research workstation.

**13 sub-agents in three trust tiers** — five Tier-1 data fetchers (two of which read untrusted news/F10 with JSON-schema-locked output), four Tier-2 analysts (fundamental, technical, whale-sentiment, quant), four Tier-3 decision agents (bull, bear, risk officer, report writer). Only the report writer can write files. Validated A-share research experience from a private quant platform is extracted into per-agent pluggable memory directories — edit a markdown, the next report uses it.

Inspired by [Anthropic's financial-services](https://github.com/anthropics/financial-services) (3-tier trust isolation, single-writer pattern) and [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) (YAML swarm presets, multi-provider LLM).

## Quick Start

```bash
git clone <repo-url>
cd financial-analyst
python -m venv .venv && source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -e .[dev]
cp .env.example .env
# Edit .env: set TUSHARE_TOKEN and ANTHROPIC_API_KEY (minimum)
financial-analyst                                      # boots TUI
```

In TUI:
```
> 看看 600519
> /agents
> /memory list bear-advocate
> /quit
```

One-shot:
```bash
financial-analyst report SH600519
```

## Architecture

```
Orchestrator -> Tier 1 (data, parallel) -> Tier 2 (analysts, parallel) -> Tier 3 (decision, serial)
```

See [docs/architecture.md](docs/architecture.md) for the full DAG.

## Extending

- Add a model: implement `BaseModel`, register in `ModelRegistry` — `model-predictor` picks it up automatically
- Add a sub-agent: implement `SubAgent`, register, add to a swarm preset yaml
- Add a knowledge base: implement `KnowledgeBase`
- Add a memory rule: drop a markdown into `memories/<agent>/`

See [docs/extending.md](docs/extending.md).

## Memory System

Each sub-agent has a `memories/<agent-name>/` directory. Files are concatenated into the agent's system prompt at runtime. Edit a memory file → next agent invocation picks it up. No restart required.

See [docs/memories.md](docs/memories.md).

## Tests

```bash
pytest tests/                # ~100 unit + integration tests, all mocked
FA_E2E=1 pytest tests/integration/test_end_to_end.py  # real Tushare + Claude
```

## License

Apache 2.0.

## Disclaimer

Drafts analyst work product for review by qualified professionals. Does not make investment recommendations, execute transactions, or post to any ledger. You are responsible for compliance with applicable laws and regulations.
