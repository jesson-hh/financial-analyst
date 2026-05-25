<p align="center">
  <h1 align="center">觀瀾 · Financial Analyst</h1>
</p>

<p align="center">
  <strong>One command. 24 AI agents. A 股深度研究.</strong>
</p>

<p align="center">
  <em>Turn a 6-digit stock code into a 16-agent deep-dive report — fundamentals · technicals · whale signals · quant scores · bull/bear/risk debate — in ~10 minutes.</em>
</p>

<p align="center">
  <strong>English</strong> &nbsp;·&nbsp; <a href="README_zh.md">中文</a>
</p>

<p align="center">
  <a href="https://pypi.org/project/financial-analyst/"><img src="https://img.shields.io/pypi/v/financial-analyst.svg?style=flat&logo=pypi&logoColor=white&label=PyPI" alt="PyPI"></a>
  <img src="https://img.shields.io/pypi/pyversions/financial-analyst.svg?style=flat&logo=python&logoColor=white" alt="Python">
  <img src="https://img.shields.io/badge/release-v1.0.1-success?style=flat" alt="Release">
  <img src="https://img.shields.io/badge/tests-712_passed-brightgreen?style=flat" alt="Tests">
  <img src="https://img.shields.io/badge/license-Apache_2.0-yellow?style=flat" alt="License">
  <br>
  <img src="https://img.shields.io/badge/agents-24-7C3AED?style=flat" alt="Agents">
  <img src="https://img.shields.io/badge/swarm_presets-5-2563EB?style=flat" alt="Swarm">
  <img src="https://img.shields.io/badge/buddy_tools-31-0F766E?style=flat" alt="Tools">
  <img src="https://img.shields.io/badge/alpha_factors-440-FF6B6B?style=flat" alt="Alphas">
  <a href="https://huggingface.co/yifishbossman"><img src="https://img.shields.io/badge/data-HF_Hub-FFD21E?style=flat&logo=huggingface&logoColor=black" alt="HF Datasets"></a>
</p>

<p align="center">
  <a href="#-what-is-it">What is it</a> &nbsp;·&nbsp;
  <a href="#-key-features">Features</a> &nbsp;·&nbsp;
  <a href="#-quick-start">Quick Start</a> &nbsp;·&nbsp;
  <a href="#-the-25-agents">Agents</a> &nbsp;·&nbsp;
  <a href="#-pluggable-memory">Memory</a> &nbsp;·&nbsp;
  <a href="#-datasets">Datasets</a> &nbsp;·&nbsp;
  <a href="#-llm-providers">LLM</a> &nbsp;·&nbsp;
  <a href="CONTRIBUTING.md">Contribute</a>
</p>

```bash
pip install financial-analyst==1.0.1    # 1 minute
financial-analyst                        # zero-config: wizard + backend + web UI + browser auto-opens
```

That's it. The first run: detects your config → runs the interactive wizard (LLM key + HF dataset pick) → starts the buddy backend on `:9999` → starts the web UI on `:5173` → opens your browser. Ctrl+C stops everything.

Power users: `financial-analyst --tui` for the terminal UI, or pick specific commands:

```bash
fa init                # wizard only (LLM key + data pack)
fa report SH600519     # one-shot deep-dive (~10 min, no UI)
fa launch              # explicit one-command launcher
```

---

## 💡 What Is It

**A-share research workstation that thinks like a buy-side analyst.**

Hand it a stock code; 14 specialized AI sub-agents run in 4 trust tiers:

```
Tier 1 (data, parallel)  →  Tier 2 (analysts, parallel)  →  Tier 3 (decision, serial)  →  Tier 4 (post-mortem)
─────────────────────       ─────────────────────────      ───────────────────────         ─────────────
quote · factors             fundamental                    bull-advocate ─┐
model · news                technical                      bear-advocate ─┤───→ writer    introspector
F10 · overseas              whale-sentiment                risk-officer   ┘
sector-rotation             quant                          (single writer)
```

Out comes a markdown research report — **rated, attributed, falsifiable**. The `report-writer` is the **only** agent allowed to write report files. Untrusted news/F10 sources are JSON-schema-locked at Tier-1 (no prompt injection). Memory is markdown — edit a `.md`, next report uses it. FTS5 retrieval cuts prompt cost ~60%.

---

## ✨ Key Features

<table>
<tr>
<td width="50%" valign="top">

### 🎯 16-agent stock deep-dive
Hand it `SH600519`, get a full research report in ~10 min — fundamentals, technicals, whale signals, quant scores, bull/bear/risk debate, post-mortem self-audit. **Only `report-writer` writes files.**

```bash
fa report SH600519
```

</td>
<td width="50%" valign="top">

### 🌅 Morning brief (5-agent v2)
Pre-market scan: overnight US + HK + VIX, A-share 异动, catalyst extraction, sector rotation, AI-written summary.

```bash
fa brief
```

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🌍 Overseas radar (v1.9.7)
International transmission analysis: SPX/NDX/HSI/VIX/USDCNY → A-share follow-through judgment + actionable signals for tomorrow.

```bash
fa overseas-radar
```

</td>
<td width="50%" valign="top">

### 📈 Monthly mainline radar
5-state industry-chain classifier (mainline / initiation / revival / decay / cold). Catches `init → mainline` golden signal (+5.54pp fwd_60d, 87% win rate).

```bash
fa mainline
```

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🧠 Pluggable memory
24 per-agent memory dirs as markdown. Edit `risk-officer/hard_rules.md`, next report respects it. No code change. `_shared/playbook_V1_V10.md` cross-agent.

</td>
<td width="50%" valign="top">

### 💤 Dream loop (self-improving)
After each report, `introspector` flags quality issues. Aggregator clusters proposals → `_proposed/` for human review. **No auto-merge** (errors compound in quant).

```bash
fa dream --since 30
```

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔌 4-provider LLM routing
`qwen` (domestic direct) · `deepseek-chat/reasoner` (Clash + MITM) · `openai` · `anthropic`. Per-provider network profile, no fake-ip hijack.

```bash
financial-analyst  # /model deepseek-reasoner
```

</td>
<td width="50%" valign="top">

### 🧬 BYOM extensibility
Drop a `.py` into `config/plugins.yaml`. Your private model joins the quant consensus. **Your checkpoints never enter the open-source repo.**

See [examples/](examples/) for FM cluster / CSV loader / TDX F10 patterns.

</td>
</tr>
</table>

---

## ⚡ Quick Start

### A. PyPI (recommended, 1 minute)

```bash
pip install financial-analyst==1.0.1
cp .env.example .env       # add DASHSCOPE_API_KEY (qwen default)
fa init                    # interactive wizard — pulls HF dataset
fa report SH600519         # first deep-dive
```

### B. Docker (zero local setup, 2 minutes)

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
cp .env.example .env
docker compose up          # → interactive TUI
```

### C. Source (development)

```bash
git clone https://github.com/jesson-hh/financial-analyst.git
cd financial-analyst
pip install -e ".[dev]"
pytest tests/              # 712 tests, ~8 min
```

---

## 🤖 The 25 Agents

| Tier | Agents | Role |
|---|---|---|
| **Tier 1** (data) | quote-fetcher · factor-computer · model-predictor · **news-reader** · **f10-reader** · overseas-market-scanner · sector-rotation-analyzer | Parallel fetch + factor + read untrusted (JSON-schema-locked) |
| **Tier 2** (analysts) | fundamental · technical · whale · quant | Per-perspective structured analysis |
| **Tier 3** (decision) | bull-advocate · bear-advocate · risk-officer · **report-writer** | Debate then synthesize (only writer can persist) |
| **Tier 4** (audit) | introspector | Post-mortem self-audit + memory proposals |
| **Market** | market-scanner · morning-brief-writer · catalyst-extractor (v1.9.7) · global-news-aggregator (v1.9.7) · macro-impact-analyzer (v1.9.7) · mainline-classifier · mainline-writer · intraday-reviewer | Cross-stock and macro pipelines |
| **Meta** | ask | Free-form Q&A via tool chain (31 buddy tools) |

Full DAG: [docs/architecture/14_agents.md](docs/architecture/14_agents.md)

---

## 🧠 Pluggable Memory

```
memories/
├── README.md                        # ← directory index, must-read
├── risk-officer/
│   ├── hard_rules.md                # ← edit this → next report uses it
│   └── pitfalls.md                  # FTS5-retrieved (large file)
├── technical-analyst/
│   └── factor_insights.md
└── _shared/
    └── playbook_V1_V10.md           # cross-agent shared
```

**Edit a markdown → next agent run picks it up. No restart, no rebuild.**

```bash
# Persist a lesson via slash command in TUI:
> /lesson Mega-cap PE>50 + 60d return>30% usually means liquidity-game stock

# Or just write the file:
vim memories/risk-officer/hard_rules.md
```

See [memories/README.md](memories/README.md) for the 24 dir index and design principles.

---

## 📊 Datasets

Three preset bundles on HuggingFace, `fa init` auto-pulls:

| Tier | Size | Stocks | 5min | Financials | F10 text | TDX zip | Repo |
|---|---|---|---|---|---|---|---|
| **demo** | ~155 MB | 300 (CSI300) | ❌ | ❌ | ❌ | ❌ | [data-demo](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-demo) |
| **lite** | ~3 GB | 800 (CSI800) | ✅ ~7d | ✅ 735 MB | ✅ 1323 codes | ❌ | [data-lite](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-lite) |
| **full** | ~14 GB | 5500+ (all A) | ✅ | ✅ | ✅ | ✅ 257 MB | [data-full](https://huggingface.co/datasets/yifishbossman/financial-analyst-data-full) |

```python
from huggingface_hub import snapshot_download
snapshot_download(
    repo_id="yifishbossman/financial-analyst-data-lite",
    repo_type="dataset",
    local_dir="~/.financial-analyst/data",
)
```

**Two binary formats**: Qlib `.bin` (time-series, `[4-byte float32 start_idx] + [float32 array]`) for OHLCV+factors; Parquet (columnar) for financials/events/F10/industry. Compatible with [Microsoft Qlib](https://github.com/microsoft/qlib) and `D.features()` API directly.

---

## 🔌 LLM Providers

| Provider | Models | Network profile | Use case |
|---|---|---|---|
| **qwen** *(default)* | `qwen3.5-plus` · `qwen3-coder-plus` | `domestic` (direct, no proxy) | 国内最快最便宜 |
| **deepseek** | `deepseek-chat` · `deepseek-reasoner` | `intl_clash` (Clash + verify=False MITM) | strong reasoning, low cost |
| **openai** | `gpt-4o` · `gpt-4-turbo` | `intl_clash` | universal fallback |
| **anthropic** | `claude-opus-4-7` · `claude-sonnet-4-6` · `claude-haiku-4-5` | litellm fallback | top quality (USD pricing) |

Switch live in TUI:
```bash
> /model deepseek-reasoner    # hot-swap, no restart
```

Or set default in `config/llm.yaml`. See [docs/llm_routing.md](docs/llm_routing.md) for the network_profile design.

---

## 🤝 Contributing

PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for:
- Development loop (branch / test / lint / changelog / PR)
- Adding a new sub-agent (registry + memory + yaml + tests)
- Adding a new data source (`net.py.domestic_session` + `@rate_limited`)
- Conventional commits ([angular preset](https://www.conventionalcommits.org/))

Other docs:
- [VERSIONING.md](VERSIONING.md) — N-2 LTS, semver policy
- [SECURITY.md](SECURITY.md) — vuln reporting (private)
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) — Contributor Covenant 2.1
- [docs/journey.md](docs/journey.md) — bilingual build journey (empty repo → 440 alphas + 25 agents, ~2 weeks)

---

## 📄 License & Disclaimer

Apache 2.0. **Research and educational purposes only**. Drafts analyst-grade work product for review by qualified professionals. Does not make investment recommendations, execute transactions, or post to any ledger. You are responsible for compliance with applicable laws.

<sub>v1.0.1 · 2026-05-25 · made by [@jesson-hh](https://github.com/jesson-hh) · bilingual zh/en</sub>
