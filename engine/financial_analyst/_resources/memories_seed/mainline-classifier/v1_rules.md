# Mainline Radar v1 — Empirical Alpha (12-month backtest)

## 5 Status definitions

| Status | Definition | fwd_60d alpha | win rate | sample |
|---|---|---|---|---|
| **mainline** | 60d strong excess + persistence + 大龙 | **+4.05pp** | 68% | n=364 |
| **revival** | 60d 仍正 + 近 20d 深回调 (V4 立讯模式候选) | +2.1pp | 62% | n=88 |
| **initiation** | 60d 尚未爆发 + 近期突增 | +1.43pp | 57% | n=143 |
| **decay** | v1 误名, 实际是主线短期回调点 | +0.49pp (fwd_10d) | 53% | n=92 |
| **cold** | 真冷门 | -0.96pp | 41% | n=287 |

## Golden signal: initiation → mainline switch

**fwd_60d +5.54pp 胜率 87% (n=15, t=3.43)** — most reliable single signal in the radar.

When `prev_status == "initiation"` AND `status == "mainline"` AND `just_switched == True`, this is the top opportunity of the month.

## Anti-signal: 大龙 participation

When status == "mainline" AND `lu_max_mv_60d_mean >= 500亿` (i.e. very-large-cap stocks lead the limit-up board in this sector):
- fwd_60d **-1.5pp** (主升后期, near top)
- DO NOT chase. The dragon being big means individual investors have already piled in.

## Lessons from v0.1/v0.2 (deprecated)

- v0.2 used daily-level breadth (top1 涨幅 / lu_count / 大龙 mv) directly as predictor — turned out to be **lagging**, fwd_5d -0.45pp.
- The fix: aggregate to **month-level** + classify into states. Don't predict; tag the regime, then position size by the regime's empirical alpha.

## V3 board integration

- mainline 行业 → V3 板块强度评分 +1 (在某只股票的研报里 V3 维度加分)
- initiation → V4 立讯模式候选
- cold → V3 强制 0 分

## Cross-link

For risk-officer veto rules + lagging signal lessons, see `memories/risk-officer/lagging_signal_lesson_D45_D46.md`.
