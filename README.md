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
> /memory search 游资       # find all memory mentioning game-capital traders
> /memory stats             # per-agent file count + bytes
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

## Extending — Bring Your Own Models (BYOM)

`financial-analyst` is a framework. Plug in your own private models / loaders / collectors via the registry pattern:

```python
# G:/my_private_code/my_fm.py
from financial_analyst.models import BaseModel, ModelRegistry

class MyFMCluster(BaseModel):
    def predict(self, code, asof):
        return {"score": ..., "rank_pct": ...}
    def metadata(self):
        return {"name": "my_fm", "version": "W10"}

ModelRegistry.register("my_fm", MyFMCluster)
```

```yaml
# config/plugins.yaml
load_at_startup:
  - G:/my_private_code/my_fm.py
```

Now `financial-analyst report SH600519` will include your model in the quant consensus.

See [docs/byom.md](docs/byom.md) for the full guide and `examples/` for 4 stub implementations (FM cluster, CSV loader, news collector, F10 collector).

Inspect what's registered:
```bash
financial-analyst models list
financial-analyst loaders list
financial-analyst agents list
financial-analyst collectors list
```

## Memory System

Each sub-agent has a `memories/<agent-name>/` directory. Files are concatenated into the agent's system prompt at runtime. Edit a memory file → next agent invocation picks it up. No restart required.

**v0.2: FTS5 retrieval mode.** Per-agent `memory_mode: retrieval` (set in preset yaml) switches inline-injection to top-K FTS5 retrieval, cutting prompt tokens ~60% for agents with large memory libraries (e.g. `bear-advocate`, `risk-officer`). The `_shared/` directory always loads in full. See [docs/memories.md](docs/memories.md).

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
