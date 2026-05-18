# financial-analyst 1.0.0 — stable release

> A-share single-stock + market-level multi-agent research workstation. Three trust tiers, pluggable memory with self-improving dream loop, BYOM extension framework, MCP server for Claude Desktop.

## What's in 1.0

This is the culmination of v0.1 -> v0.10 — a full open-source quantitative research framework for A-share investors.

### Agent system (20 agents)
- **Single-stock deep-dive** (13 agents): quote-fetcher / factor-computer / model-predictor / news-reader / f10-reader -> fundamental / technical / whale / quant analysts -> bull / bear / risk-officer / report-writer
- **Market-level** (5 agents): mainline-classifier / mainline-writer / market-scanner / morning-brief-writer / intraday-reviewer
- **Meta** (2 agents): introspector (dream loop) / ask (front-desk Q&A)

### Workflows
- `financial-analyst report SH600519` — full 13-agent deep-dive (~10 min)
- `financial-analyst ask "..."` — natural-language with 6 tools (~30s)
- `financial-analyst mainline` — monthly sector radar (5 states + star golden signal)
- `financial-analyst brief` — daily market scan (mv-tier-aware thresholds)
- `financial-analyst intraday` — lunch-break OK / caution / exit verdict
- `financial-analyst dream` — agent self-introspection -> memory proposals
- `financial-analyst ingest` — CSV -> Qlib binary onboarding

### Data layer
- Tushare (HTTP + ParquetCache) / QlibBinaryLoader (day + 5min) / CsvIngester
- BYOM via `config/plugins.yaml` — your private models / loaders / collectors

### Memory system
- Per-agent `memories/<agent>/*.md` + `_shared/` + `always_include.txt`
- FTS5 retrieval (CJK-safe) for token-efficient prompts
- Hot reload — edit a markdown, next agent run uses it
- Dream-loop staging: `memories/_proposed/` -> `/memory accept` / `reject`

### Validated alpha (ported from G:\stocks)
- **Mainline Radar v1**: mainline fwd_60d **+4.05pp win rate 68%** / initiation->mainline switch **+5.54pp win rate 87%**
- **R7-R20 sentiment signals**: super_distr (SS, fwd_5d **-4.20pp**) / distr (S) / tail_surge / bounce / board v5
- **Market-cap tier rating system v4**: large-cap >=100B -> factor score forced 0, mid-cap capped +-1, small-cap full +-2
- **F1-F14 failure modes**: game-capital ticker veto / lagging signal trap / goodwill risk / etc

### Integration
- **MCP server**: 12 tools for Claude Desktop / Code / OpenClaw
- **PyPI**: `pip install financial-analyst`
- **Docker**: `docker compose up`

## Three install paths

```bash
# A. PyPI (recommended)
pip install financial-analyst

# B. Docker
git clone https://github.com/jesson-hh/financial-analyst.git && cd financial-analyst
docker compose up

# C. Source
git clone https://github.com/jesson-hh/financial-analyst.git && cd financial-analyst && pip install -e .[dev]
```

## Stability commitment

From 1.0 onwards, all public APIs follow semver. Breaking changes require major bump.
v1.x focuses on stability + ecosystem expansion (more collectors / models / preset templates), not protocol changes.

## What's NEXT (post-1.0)

- v1.1: more market-level presets (sector rotation / earnings season)
- v1.2: Akshare + yfinance ingesters realized (currently stubs)
- v1.3: streaming Web UI (optional, for users who want it)
- v2.0: cross-market support (if community demand)

## Acknowledgments

- Architecture inspiration: [Anthropic financial-services](https://github.com/anthropics/financial-services) (three-tier trust isolation, sole-writer pattern)
- Multi-agent DAG: [HKUDS Vibe-Trading](https://github.com/HKUDS/Vibe-Trading) (YAML swarm presets, multi-provider LLM)
- Validated alpha sources: private G:\stocks research repo (18-round sentiment study, mainline radar v1, V1-V10 analyst playbook)

## Full changelog
See [CHANGELOG.md](https://github.com/jesson-hh/financial-analyst/blob/main/CHANGELOG.md) for the v0.1 -> v1.0 evolution.

---

Generated with [Claude](https://claude.com/claude-code) — built end-to-end in a single multi-day collaborative session.
