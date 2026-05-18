# Intraday Review (v0.8-B)

Lunch-break per-stock verdict. Designed for 11:35-13:00 window — judge each held position OK / 警惕 / 撤离 before afternoon session opens.

## Quick start

```bash
# Explicit codes
financial-analyst intraday --codes SH600519,SZ000858,SH601318 --asof 2026-05-18

# Auto-detect from recent reports in out/
financial-analyst intraday --asof 2026-05-18

# TUI
> /intraday --codes=SH600519,SZ000858
```

## Output

- `out/intraday_review_<date>.md` — per-stock verdict + afternoon plan
- `out/intraday_review_<date>.json` — structured

## Three verdicts

| Verdict | When | Afternoon action |
|---|---|---|
| **OK** | direction matches expectation | continue, watch close |
| **警惕** | mild adverse move (-2% ~ -5%) | watch 14:30, exit if breaks |
| **撤离** | stop_loss hit OR -8% drop OR thesis broken | exit at PM open, market order |

## Lunch-break discipline

- 不在 11:35-13:00 加仓
- 撤离单排队到 13:00 PM open, 市价清
- 不"猜底" — 上午大跌的票, 下午追入胜率 < 50%

## Workflow

```bash
# 1. Morning brief (around 9:00, before market open)
financial-analyst brief --asof today

# 2. Run reports on watchlist (~30 min total)
financial-analyst report SH600519
# ...

# 3. AT LUNCH BREAK (11:35-13:00):
financial-analyst intraday   # auto-detects yesterday's reports

# 4. Read the verdicts, act on 撤离 list at 13:00 PM open
# 5. (Optional) /dream weekly to introspect & propose memory updates
```
