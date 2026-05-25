# Data Contract / 数据契约

> One doc. Where data lives, what units, who writes, who reads.
> 一份契约说清: 数据放哪 / 单位是啥 / 谁写 / 谁读.

This is the **single source of truth** for data layout in financial-analyst.
本文是 financial-analyst 数据布局的 **唯一真理源**.

If you find a hardcoded path that contradicts this doc, **the doc wins** —
file an issue or fix the code to use `financial_analyst.data.paths.get_data_paths()`.

---

## 1. Bird's-eye view / 总览

```
                   ┌────────────────────────────────┐
                   │  G:/stocks  (research lab)     │
                   │  produces & maintains          │
                   │  · cn_data/   (Qlib day bins)  │
                   │  · cn_data_5min/               │
                   │  · parquet/   (non-time-series)│
                   │  · news_data/tdx_f10/          │
                   └──────────────┬─────────────────┘
                                  │
                                  │  publish_hf_dataset.py
                                  ▼
        ┌──────────────────────────────────────────────────┐
        │  HuggingFace Hub                                 │
        │  yifishbossman/financial-analyst-data-{demo,     │
        │                                lite, full}       │
        └──────────────────┬───────────────────────────────┘
                           │  `fa init` (snapshot_download)
                           ▼
        ┌──────────────────────────────────────────────────┐
        │  ~/.financial-analyst/data/  (first-user)        │
        │  Same layout as research lab                     │
        └──────────────────┬───────────────────────────────┘
                           │  `qlib.init` + `pd.read_parquet`
                           ▼
        ┌──────────────────────────────────────────────────┐
        │  financial-analyst (public package)              │
        │  reads via `data.paths.get_data_paths()`         │
        └──────────────────────────────────────────────────┘
```

There are **two valid layouts**, both with identical sub-directory structure:

| Layout | Root | Used by |
|---|---|---|
| **Dev / research lab** | `G:/stocks/stock_data/` + `G:/stocks/news_data/` | The author (editable install). `G:/stocks/config.py` is the Python truth source. |
| **First user** | `~/.financial-analyst/data/` | Anyone who `pip install financial-analyst` + `fa init`. Populated by HuggingFace snapshot download. |

---

## 2. Directory Layout / 目录布局

The same tree is used in both layouts; only the **root** differs.

```
{ROOT}/
├── cn_data/                       # Qlib day-frequency binary
│   ├── calendars/day.txt          # trading calendar (UTF-8, one date per line)
│   ├── instruments/all.txt        # universe membership (code + start..end)
│   └── features/{code}/           # one dir per stock, e.g. sh600519/
│       ├── open.day.bin           # [4-byte float32 start_idx] + [float32 array]
│       ├── high.day.bin
│       ├── low.day.bin
│       ├── close.day.bin
│       ├── volume.day.bin
│       ├── amount.day.bin
│       ├── pe_ttm.day.bin
│       ├── pb.day.bin
│       ├── ps_ttm.day.bin
│       ├── dv_ttm.day.bin
│       ├── total_mv.day.bin
│       ├── circ_mv.day.bin
│       └── turnover_rate.day.bin
│
├── cn_data_5min/                  # Same layout, freq=5min
├── cn_data_1min/                  # Same layout, freq=1min (optional)
│
├── parquet/                       # Non-time-series, columnar
│   ├── tushare_stock_basic.parquet      # all listed tickers (code → name, industry, mv)
│   ├── industry_boards.parquet          # 同花顺 level-1 industry classification
│   ├── concept_ths_*.parquet            # 同花顺 concept boards
│   ├── index_constituents.parquet       # CSI300 / CSI500 / ... membership
│   ├── tdx_f10_index.parquet            # F10 event index (date · code · type · summary)
│   ├── tdx_f10_warnings_latest.parquet  # last-7-day negative warnings (utility filter)
│   ├── northbound_holding.parquet       # northbound stake (HK Connect)
│   ├── ipo_info.parquet                 # IPO listing dates
│   ├── instruments.parquet              # universe-level meta
│   ├── fincast_daily_pred.parquet       # FinCast model forecast (research artifact)
│   ├── financial/                       # full reports — assets/liabilities/income/cashflow/indicators
│   ├── events/                          # company announcements
│   ├── institutional/                   # institutional holdings
│   ├── blocks/                          # block-trade records
│   └── xdxr/                            # dividend + split adjustment factors
│
├── tdx_finance/                   # (full preset only) raw TDX historical financial zip
│                                  # — unpack via scripts/import_tdx_financial.py
│
└── news_data/                     # Cross-cutting raw text (NOT a Qlib root)
    └── tdx_f10/{code}/            # per-stock F10 event text (公司大事/龙虎榜单/...)
```

---

## 3. Path Resolution / 路径解析

All callers must use `financial_analyst.data.paths.get_data_paths()`.

**Resolution priority** (each path resolved independently — you may mix sources):

1. **Env var**
   - `FA_QLIB_URI` — overrides `qlib_uri`
   - `FA_PARQUET_ROOT` — overrides `parquet_root`
   - `FA_NEWS_DATA_ROOT` — overrides `news_data_root`
2. **`config/loaders.yaml`** — keys under `loaders.qlib_binary.{provider_uri, parquet_root, news_data_root}`
3. **`~/.financial-analyst/data/`** — after `fa init` has downloaded a HF preset
4. **`G:/stocks/stock_data/`** + **`G:/stocks/news_data/`** — dev fallback (author's machine)

```python
from financial_analyst.data.paths import get_data_paths

paths = get_data_paths()
paths.qlib_uri        # str or {"day": ..., "5min": ...} — pass to qlib.init
paths.qlib_day        # Path — always resolvable
paths.qlib_5min       # Path | None
paths.parquet_root    # Path — for pd.read_parquet
paths.news_data_root  # Path — root of news / F10 raw text
paths.tdx_f10_root    # Path — convenience accessor for news_data_root/tdx_f10
```

After `fa init` runs successfully, it **rewrites `config/loaders.yaml`** to point
at `~/.financial-analyst/data/`. Dev users editing the repo keep the bundled
defaults pointing at `G:/stocks/stock_data/`.

---

## 4. Field Tables / 字段表

### 4.1 OHLCV (Qlib binary)

| Field | dtype | Unit | Source | Notes |
|---|---|---|---|---|
| `open` / `high` / `low` / `close` | float32 | 元 / CNY | Tushare `pro.daily` | **Not adjusted** (前复权). No split/dividend backfill in bins. |
| `volume` | float32 | 手 (= 100 shares) | Tushare convention | **NOT** pytdx convention (which uses 股). |
| `amount` | float32 | 元 / CNY | Tushare | Turnover value. |

### 4.2 Valuation (Qlib binary, `VALUATION_FIELDS` in `G:/stocks/config.py:64`)

| Field | Unit | Source | Notes |
|---|---|---|---|
| `pe_ttm` | dimensionless | Tushare `daily_basic` | TTM P/E. |
| `pb` | dimensionless | Tushare `daily_basic` | Latest P/B. |
| `ps_ttm` | dimensionless | Tushare `daily_basic` | TTM P/S. May be NaN for tushare-free tier. |
| `dv_ttm` | % | Tushare `daily_basic` | TTM dividend yield. May be NaN. |
| `total_mv` | **万元 (10K CNY)** | Tushare `daily_basic` | Total market cap. |
| `circ_mv` | **万元 (10K CNY)** | Tushare `daily_basic` | Circulating market cap. |
| `turnover_rate` | % | Tushare `daily_basic` | Daily turnover ratio. |

### 4.3 Factors (Qlib binary, 34 base → 40 with V5/V6/V7)

Authoritative list: `G:/stocks/strategy/factor_insights.md` + `G:/stocks/strategy/factors/*.csv`.
Monitored by `G:/stocks/strategy/factors/ic_monitor.py` (currently 27 csv files; the V5/V6/V7
additions are tracked in `strategy/log.md` 2026-Q2 entries and need to be re-synced into
ic_monitor — see TODO in CLAUDE.md).

### 4.4 Whale signals (in `_agent_ctx/{CODE}.json`, generated by `report_v2.py`)

Documented in `G:/stocks/CLAUDE.md` §"`_agent_ctx/{CODE}.json` 数据契约" — fields
include `whale_judge`, `obv_trend`, `vr_judge`, `mfi_judge`, `shadow_judge`, `chip_judge`.

### 4.5 Sentiment / 涨停信号 (in `_agent_ctx`)

- `board_score` (v4 four-dim, range -4..+5) + `board_seal_score` (v5 micro, -3..+3)
   → `board_total_score` range -7..+8 (threshold ≥4 = first-board worth chasing)
- `vol_regime` — 5-state classifier (super_distr/distr/bounce/tail_surge/neutral)
   → expected_spread_pp from R14 empirical study

### 4.6 TDX F10 (parquet index + raw text)

- Index: `parquet/tdx_f10_index.parquet` (~263KB, 5122 rows)
- Latest negative warnings: `parquet/tdx_f10_warnings_latest.parquet` (~7KB, 66 rows)
- Raw text: `news_data/tdx_f10/{code}/{type}_{date}.txt`

15 event types are extracted (公司大事/龙虎榜单/主力追踪/最新提示/研究报告/…).
Game-capital whitelist of 30+ named traders in `tdx_f10_collector.KNOWN_GAME_CAPITAL`.

---

## 5. Writer / Reader Matrix / 谁写谁读

| Writer (single entry) | Output | Readers |
|---|---|---|
| `scripts/incremental_update_tushare.py` (in `G:/stocks/`) | `cn_data/features/{code}/*.day.bin` (OHLCV + `VALUATION_FIELDS`) | qlib.D, all factor scripts, report_v2.py |
| `scripts/import_tdx_5min.py` (in `G:/stocks/`) | `cn_data_5min/features/{code}/*.5min.bin` | board_scorer v5, volume_regime, intraday |
| `strategy/factors/*.py` | `factors/*.csv` (per-factor cross-sectional IC) | ic_monitor, v4_ranking |
| `src/data/tdx_f10_collector.py` | `news_data/tdx_f10/{code}/*.txt` + `parquet/tdx_f10_index.parquet` | report_v2 knowledge_pack, rule_signal_generator |
| `scripts/scan_negative_events.py` | `parquet/tdx_f10_warnings_latest.parquet` | rule_signal_generator (severity≥2 = hard sell) |
| `scripts/publish_hf_dataset.py` | `.staging_hf/` → HF Hub | First-user via `fa init` |

**Hard rule** (see `G:/stocks/CLAUDE.md` and `G:/stocks/scripts/_deprecated/README.md`):
Daily bin writes **must** go through `src.data.bin_writer.safe_merge_write`. Raw
`write_bin` is for full reimports only. The 2026-04-14 data-overwrite incident
removed 2 500+ days of `pe_ttm / pb / ps_ttm / dv_ttm / total_mv / circ_mv /
turnover_rate` history because a duplicate "pull → write_bin" pipeline forgot the
merge guard. **Do not** create new daily-pull scripts.

---

## 6. Common Pitfalls / 常见坑

| Pitfall | Why it bites |
|---|---|
| Reading `volume` as 股 not 手 | Tushare stores 手; multiplying by 100 doubles up if you also use 万元 elsewhere. |
| Reading `total_mv` as 元 not 万元 | Off by 4 orders of magnitude. The unit is **万元** per the `daily_basic` schema. |
| Calling `qlib.init` per script | Qlib uses a process-global state; re-init crashes. Call once at module entry. |
| Hardcoding `G:/stocks/...` | Breaks for non-dev users. Use `get_data_paths()`. |
| Calling `D.features` before `qlib.init` | Silent empty DataFrame. Always init with `PROVIDER_URI_MAP`. |
| Writing daily bins without merge | Overwrites pre-existing history. Use `safe_merge_write`. |
| Forgetting `start_idx` in raw bin reads | First 4 bytes are the calendar offset, not data. |

---

## 7. References / 引用

- `G:/stocks/config.py` — Python truth source for paths + field lists
- `G:/stocks/CLAUDE.md` — research-side workflow and data-source single entry
- `G:/stocks/scripts/_deprecated/README.md` — 2026-04-14 incident post-mortem
- `G:/stocks/strategy/factor_insights.md` — verified factor library
- `G:/stocks/strategy/rating_system.md` — v4 rating system (mv-tiered)
- `huggingface.co/datasets/yifishbossman/financial-analyst-data-{demo,lite,full}`
- `financial_analyst.data.paths.get_data_paths()` — the only sanctioned path API

---

<sub>v1.0.1 · 2026-05-25 · part of the financial-analyst public release</sub>
