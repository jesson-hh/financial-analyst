# Financial Analyst

**English** | [中文](README_zh.md)

[![PyPI](https://img.shields.io/pypi/v/financial-analyst.svg)](https://pypi.org/project/financial-analyst/)
[![Python](https://img.shields.io/pypi/pyversions/financial-analyst.svg)](https://pypi.org/project/financial-analyst/)
[![tests](https://img.shields.io/badge/tests-705_passed-brightgreen)](https://github.com/jesson-hh/financial-analyst/actions)
[![license](https://img.shields.io/badge/license-Apache_2.0-green)](LICENSE)
[![status](https://img.shields.io/badge/status-stable_1.9.6-success)](https://github.com/jesson-hh/financial-analyst/releases)
[![alphas](https://img.shields.io/badge/alphas-440-blue)](docs/journey.md)
[![dataset](https://img.shields.io/badge/data-HF_Hub-yellow)](https://huggingface.co/yifishbossman)

**A-share single-stock deep-dive multi-agent research workstation.**

📖 New here? Read the [build journey & architecture overview](docs/journey.md) (中英双语 / bilingual) — a two-week retrospective from empty repo to 440 alphas + 21 sub-agents.

**14 sub-agents in four trust tiers** — five Tier-1 data fetchers (two of which read untrusted news/F10 with JSON-schema-locked output), four Tier-2 analysts (fundamental, technical, whale-sentiment, quant), four Tier-3 decision agents (bull, bear, risk officer, report writer), and one Tier-4 post-mortem introspector that runs after the writer to self-audit and propose memory updates for human review. Only the report writer can write report files. Memory is pluggable per-agent (`memories/<agent>/*.md`) — edit a markdown, the next report uses it. FTS5-backed retrieval keeps prompt costs ~60% lower than naive full-injection.

See [docs/architecture/14_agents.md](docs/architecture/14_agents.md) for the full DAG + I/O schemas.

Inspired by [Anthropic's financial-services](https://github.com/anthropics/financial-services) (3-tier trust isolation, single-writer pattern) and [HKUDS/Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) (YAML swarm presets, multi-provider LLM).

## What's in v1.0

- **25 agents total** (v1.9.7): 14 single-stock (data → analyst → decision → introspector, see `config/swarm/stock-deep-dive.yaml`) + 10 market-level (incl. morning-brief v2 with overseas/catalyst/rotation + new overseas-radar with global news/macro impact) + 1 meta (ask)
- **8 swarm presets**: stock-deep-dive, morning-brief (5-agent v2), overseas-radar (new), mainline-radar, intraday-review, dream + more
- **QlibBinaryLoader** (day + 5min) + **TushareLoader** (HTTP + ParquetCache) + **CSV ingester**
- **R7-R20 sentiment signals** (board_scorer v5, volume_regime super_distr/tail_surge, whale signals)
- **Pluggable memory** with FTS5 retrieval + always_include white-list + `_shared/` cross-agent playbook
- **Dream loop** — agent self-improving memory (OutcomeTracker + Introspector + `memories/_proposed/` staging + human accept/reject)
- **BYOM** — registry-based plug-in points for models / loaders / collectors / sub-agents / KBs; `config/plugins.yaml` auto-loads user `.py` files at startup
- **12 MCP tools** for Claude Desktop / Claude Code integration
- **290 tests** unit + integration (mocked LLM) + 1 opt-in real E2E test

## Quick Start

Three paths to your first report:

### A. PyPI install (recommended, 1 minute)

```bash
pip install financial-analyst
cp .env.example .env   # edit: TUSHARE_TOKEN + DASHSCOPE_API_KEY
financial-analyst                  # boot TUI
```

### B. Docker (zero local setup, 2 minutes)

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
cp .env.example .env   # edit your keys
docker compose up      # → interactive TUI
```

Run one-shot inside container:
```bash
docker compose run --rm fa report SH600519
docker compose run --rm fa ask "PE of SH600519"
```

### C. Source (development)

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
python -m venv .venv && .venv\Scripts\activate    # Windows; or `source .venv/bin/activate`
pip install -e .[dev]
cp .env.example .env
financial-analyst
```

## First commands

In TUI:
```
> 看看 600519                         # 完整深度研报 (~10 min)
> /ask SH600519 现在 PE 多少           # 秒级问答
> /mainline                          # 月级主线雷达
> /brief                             # 早盘异动扫描
> /intraday                          # 午休复盘
> /memory search 游资                 # 搜经验库
> /dream --since 30                  # 经验自迭代
> /sessions new my-project           # 多会话切换
> /quit
```

One-shot:
```bash
financial-analyst report SH600519 --asof 2026-05-15
financial-analyst ask -f question.txt
financial-analyst report -f codes.txt --trace
echo "SH600519 PE 多少" | financial-analyst ask
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

## MCP Server

Use financial-analyst from Claude Desktop / Claude Code via MCP:

```json
{
  "mcpServers": {
    "financial-analyst": {
      "command": "financial-analyst-mcp",
      "env": {"TUSHARE_TOKEN": "...", "DASHSCOPE_API_KEY": "..."}
    }
  }
}
```

12 tools exposed (ask / quick_quote / quick_factors / memory_search / list_past_reports / read_past_report / list_dream_proposals / mainline / brief / intraday / report / dream). See [docs/mcp.md](docs/mcp.md).

## Tests

```bash
pytest tests/                                       # 291 unit + integration tests (mocked)
FA_E2E=1 pytest tests/integration/test_end_to_end.py  # real Tushare + LLM round-trip
```

## License

Apache 2.0.

## Disclaimer

Drafts analyst work product for review by qualified professionals. Does not make investment recommendations, execute transactions, or post to any ledger. You are responsible for compliance with applicable laws and regulations.
