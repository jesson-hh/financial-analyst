# Mainline Radar (v0.7)

Monthly market-structure scanner. Classifies sectors into 5 states, detects golden signals (initiation → mainline switch).

## Quick start

```bash
# Default: tries G:/stocks/strategy/mainline/monthly_mainlines_panel.parquet
financial-analyst mainline --asof 2026-05-15

# Or point at your own panel
financial-analyst mainline --asof 2026-05-15 --panel /path/to/panel.parquet

# In TUI
> /mainline --asof=2026-05-15
```

## Empirical alpha (12-month backtest)

| Status | fwd_60d | win rate | sample |
|---|---|---|---|
| mainline | **+4.05pp** | 68% | n=364 |
| revival | +2.1pp | 62% | n=88 |
| initiation | +1.43pp | 57% | n=143 |
| decay (misnamed) | +0.49pp (10d) | 53% | n=92 |
| cold | -0.96pp | 41% | n=287 |

**Golden signal**: `initiation → mainline` switch → fwd_60d **+5.54pp 胜率 87%** (n=15, t=3.43).

**Anti-signal**: mainline + `lu_max_mv_60d_mean >= 500亿` → fwd_60d -1.5pp (主升后期, 不追高).

## Panel format

The radar reads a parquet file with columns:
- `datetime` (monthly snapshots)
- `industry`
- `status` (one of mainline / revival / initiation / decay / cold / neutral)
- `ex_60d`, `ex_20d`, `ex_10d` (excess return vs index)
- `top10_ratio_60d` (% of top-10 ranked days in last 60)
- `lu_count_60d_sum` (limit-up count sum in last 60)
- `lu_max_mv_60d_mean` (avg max-mv of limit-up board, 大龙 indicator)

The panel is computed OFFLINE (sector-level aggregation of stock-level data over a 60-day rolling window). See `G:\stocks/strategy/mainline/compute_monthly_mainlines.py` for the reference implementation.

For users without G:\stocks, you can compute your own panel from akshare or your data source. The radar agent only reads — it doesn't compute.

## Integration with stock deep-dive

Mainline radar is META — it informs the V3 板块强度 dimension of single-stock reports.

When you run `financial-analyst report SH600519`, the technical-analyst doesn't currently consult mainline radar. v0.8+ will add a `quote-fetcher.industry` field + an integration that says "your stock is in 'AI算力' which is currently mainline → V3 +1".
