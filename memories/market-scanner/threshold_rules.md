# Market-cap Tiered Move Thresholds

Daily pct_change considered "异动" only if exceeds the threshold for the stock's market cap:

| Tier | mv_yi (亿) | Daily pct threshold |
|---|---|---|
| 大盘 | >= 1000 | +-3% |
| 中盘 | 300 - 1000 | +-4% |
| 中小盘 | 100 - 300 | +-5% |
| 小盘 | < 100 | +-7% |

(Source: G:\stocks/strategy/morning_brief.py move_threshold().)

## Volume rule
Any tier with volume_ratio >= 3 (today's vol / 20-day avg) is also flagged.
A stock can be flagged by pct OR by volume OR both (flagged_by list).

## Why mv-tier?
Small-caps swing +-5-7% on noise; flagging them at +-3% would drown the brief in noise.
Large-caps moving +-3% IS news.

## What flagging does NOT mean
Flagging only says "moved abnormally". It doesn't say which direction is correct,
or whether to buy/sell. The morning-brief-writer + later deep-dive does that.
