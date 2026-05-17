# R7-R20 Sentiment Signals (14 S/SS-grade)

## SS-grade (super-strong, monthly 11+/12 hit rate)
- **R14 super_distr**: ret_20d>=10% AND tr_surge_60>=2.5 AND tail_surge → fwd_5d -4.20pp
- **R12 seal_at_close=False**: limit-up day broken by close → fwd_5d most negative

## S-grade (strong, monthly 12+/13)
- **R9 distr**: ret_20d>=10% AND tr_surge_60>=2.5 → -1.42pp
- **R11 tail_surge**: ret_close_30m>2% AND vs_close_30m>18% → -1.40pp
- **R12 seal_bar<=1** (instant seal) → +2 in board_score
- **R9 bounce**: down_mild + volume_surge → +0.94pp
- **R12 1次破板 (open_count=1)** → worst tier in monotonic test
- **R11 high pct_range_5d (>30%)** → distribution likely

## Application rules
- Trigger `vol_regime` warning in §四-C of report when super_distr / distr / tail_surge fire
- For first-board candidates, require `total_score>=4` (v4+v5)
- For pure long signals, R9 bounce + intact uptrend is allowed
- Game-capital tickers EXEMPT — model signals unreliable for them
