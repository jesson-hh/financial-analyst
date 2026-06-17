# Board Scorer v5

## v4 dims (-4..+5)
- turnover surge >2x avg-60: +1/+2
- t1_tr_surge >3x: +2
- amount log >19: +1
- pct_range_5d <0.05: +1, >0.30: -1
- mv<100亿: +1

## v5 micro dim (-3..+3)
- seal_bar <=1: +2 (instant seal)
- seal_bar <=6: +1 (pre-10:00)
- seal_bar >=42: -2 (tail-end)
- seal_bar >=24: -1 (afternoon)
- seal_at_close=False: -2 (broken by close)
- gap_open >=9%: +1 (one-word board)
- open_count==1: -1 (single break)

## Total range: -7..+8
## Operation threshold: total_score>=4 for first-board entry
