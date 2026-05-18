# Financial Analyst

[![PyPI](https://img.shields.io/pypi/v/financial-analyst)](https://pypi.org/project/financial-analyst/)
[![tests](https://img.shields.io/badge/tests-240_passed-brightgreen)](https://github.com/jesson-hh/financial-analyst/actions)
[![python](https://img.shields.io/badge/python-3.11+-blue)](https://www.python.org/)
[![license](https://img.shields.io/badge/license-Apache_2.0-green)](LICENSE)

**A-share single-stock deep-dive multi-agent research workstation.**

**13 sub-agents in three trust tiers** — five Tier-1 data fetchers (two of which read untrusted news/F10 with JSON-schema-locked output), four Tier-2 analysts (fundamental, technical, whale-sentiment, quant), four Tier-3 decision agents (bull, bear, risk officer, report writer). Only the report writer can write files. Memory is pluggable per-agent (`memories/<agent>/*.md`) — edit a markdown, the next report uses it. FTS5-backed retrieval keeps prompt costs ~60% lower than naive full-injection.

Inspired by [Anthropic's financial-services](https://github.com/anthropics/financial-services) (3-tier trust isolation, single-writer pattern) and [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) (YAML swarm presets, multi-provider LLM).

## What's in v0.6

- **13 sub-agent DAG** (data → analyst → decision), Qwen/Claude/DeepSeek/Ollama via LiteLLM
- **QlibBinaryLoader** (day + 5min) + **TushareLoader** (HTTP + ParquetCache) + **CSV ingester**
- **R7-R20 sentiment signals** (board_scorer v5, volume_regime super_distr/tail_surge, whale signals)
- **Pluggable memory** with FTS5 retrieval + always_include white-list + `_shared/` cross-agent playbook
- **Dream loop** — agent self-improving memory (OutcomeTracker + Introspector + `memories/_proposed/` staging + human accept/reject)
- **BYOM** — registry-based plug-in points for models / loaders / collectors / sub-agents / KBs; `config/plugins.yaml` auto-loads user `.py` files at startup
- **Markdown + JSON + HTML** output (Rich Markdown rendering + standalone HTML file)
- **226 tests** unit + integration (mocked LLM)

## Quick Start

**One-line install (when v0.6.0 is on PyPI):**

```bash
pip install financial-analyst
```

Or **from source** (development):

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
python -m venv .venv && .venv\Scripts\activate    # Windows; or `source .venv/bin/activate`
pip install -e .[dev]
```

Then:

```bash
cp .env.example .env
# Edit .env: set TUSHARE_TOKEN + your LLM provider key (default config uses DASHSCOPE_API_KEY for Qwen)
financial-analyst                                  # boots TUI
```

In TUI:

```
> 看看 600519
> /ask 我最近研报里 bear 段是不是过激了
> /agents
> /memory search 游资
> /show
> /dream --since 30
> /memory list-proposals
> /quit
```

One-shot:

```bash
financial-analyst report SH600519
financial-analyst ask "SH600519 现在 PE 多少"
financial-analyst ingest --source my_csv
financial-analyst dream --since 30
financial-analyst models list
```

## Architecture

```
Orchestrator → Tier 1 (data, parallel) → Tier 2 (analysts, parallel) → Tier 3 (decision, serial)
                  ↓                          ↓                              ↓
              fetch + factor              4 analysts                  bull/bear/risk → writer
              + read untrusted            consume tier 1 JSON         consume tier 2 JSON
              with JSON schemas
```

See [docs/architecture.md](docs/architecture.md) for the full DAG and trust model.

## Extending — Bring Your Own Models (BYOM)

`financial-analyst` is a **framework**, not a fixed product. Plug in your own private models / loaders / collectors:

```python
# G:/my_private_code/my_fm.py
from financial_analyst.models import BaseModel, ModelRegistry

class MyFMCluster(BaseModel):
    def predict(self, code, asof):
        return {"score": ..., "rank_pct": ..., "cluster": ...}
    def metadata(self):
        return {"name": "my_fm", "version": "W10"}

ModelRegistry.register("my_fm", MyFMCluster)
```

```yaml
# config/plugins.yaml
load_at_startup:
  - G:/my_private_code/my_fm.py
```

Now `financial-analyst report SH600519` includes your model in the quant consensus. **No proprietary checkpoint enters the open-source repo.**

See [docs/byom.md](docs/byom.md) for the full guide. Examples for FM cluster / CSV loader / Tushare news collector / pytdx F10 collector are in [`examples/`](examples/).

## Data Ingestion

If you don't have a Qlib data directory, ingest your CSVs:

```yaml
# config/data_sources.yaml
sources:
  - name: my_csv
    type: csv
    path: G:/my_data/*.csv
    code_col: ts_code
    date_col: trade_date
    target: ~/.financial-analyst/data/my_csv
```

```bash
financial-analyst ingest --source my_csv
# Then point config/loaders.yaml `qlib_binary.provider_uri.day` at the target
```

See [docs/data_ingest.md](docs/data_ingest.md).

## Memory System

Each sub-agent has `memories/<agent-name>/` with markdown files. Files are appended to the agent's system prompt at runtime. v0.2+: FTS5 retrieval keeps prompt cost down for agents marked `memory_mode: retrieval`. Critical files listed in `memories/<agent>/always_include.txt` are loaded unconditionally.

See [docs/memories.md](docs/memories.md) for principles, file-organization advice, and CLI commands.

## Dream Loop — Agent Self-Improving Memory

Run `/dream` (or `financial-analyst dream`) to:
1. Score past reports against T+5d / T+20d actual prices.
2. Have an introspector sub-agent identify patterns in wrong predictions.
3. Stage proposals in `memories/_proposed/<agent>/` for human review.
4. `/memory accept` merges; `/memory reject` discards.

**Auto-accept is intentionally NOT implemented** — incorrect memory updates compound losses in quant systems. Human review only.

See [docs/dream_loop.md](docs/dream_loop.md).

## Tests

```bash
pytest tests/                                       # 240 unit + integration tests (mocked)
FA_E2E=1 pytest tests/integration/test_end_to_end.py  # real Tushare + LLM round-trip
```

## License

Apache 2.0.

## Disclaimer

Drafts analyst work product for review by qualified professionals. Does not make investment recommendations, execute transactions, or post to any ledger. You are responsible for compliance with applicable laws and regulations.
