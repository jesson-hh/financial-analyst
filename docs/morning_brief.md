# Morning Brief (v0.8)

Daily A-share market-wide scanner + LLM-written brief. Detects 异动 stocks by market-cap-tier-aware thresholds.

## Quick start

```bash
# Default: scan whole universe (from your configured loader's instruments file)
financial-analyst brief --asof 2026-05-15

# Limit scan size (faster)
financial-analyst brief --asof 2026-05-15 --max-scan 1000

# Custom universe file
financial-analyst brief --asof 2026-05-15 --universe-file /path/to/csi300.txt

# TUI
> /brief --asof=2026-05-15
```

## Output

- `out/morning_brief_<date>.md` — full markdown brief
- `out/morning_brief_<date>.json` — structured (key metrics + watchlist)

## 异动 thresholds (by market cap)

| Tier | Threshold | Reason |
|---|---|---|
| 大盘 (>=1000亿) | +-3% | 大盘股 +-3% IS news |
| 中盘 (300-1000) | +-4% | |
| 中小盘 (100-300) | +-5% | |
| 小盘 (<100) | +-7% | 小盘震荡多, 阈值放宽 |
| (any) volume_ratio >= 3 | OR | 量能异常即使价格未动也值得关注 |

## Workflow

The brief itself is just identifying WHAT moved. To understand WHY:

1. Run `financial-analyst brief` in the morning
2. Read the watchlist (3-5 codes the LLM picked)
3. For each, run `financial-analyst report <code>` for full deep-dive
4. Or `financial-analyst ask "为什么 SH600519 今天涨"` for a quick LLM-curated answer
