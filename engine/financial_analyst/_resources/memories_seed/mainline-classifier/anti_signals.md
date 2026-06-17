# Anti-Signals — when NOT to trust the radar

## 1. 大龙 mv 太大 → 主升后期
Already in v1_rules.md. If `lu_max_mv_60d_mean >= 500亿` in a mainline, fwd_60d -1.5pp. Trim, don't add.

## 2. decay 状态名字误导
v1 calls a status "decay" but empirically these sectors **rebound** (fwd_10d +0.49pp). What it actually captures is mainline sectors taking a short-term breather. Treat as revival candidates, NOT bear signal.

## 3. Single-day switches are noisy
A sector that switches initiation → mainline for ONE day and immediately switches back is statistical noise. Wait for 2+ day persistence before pushing capital.

## 4. Index-level confounder
If the broad market (CSI300) is rallying 5%+ in a month, "all sectors look strong". Subtract the index return before judging mainline strength.

## 5. Sector classification drift
Tushare/akshare 行业分类 occasionally re-categorizes stocks. If a sector's constituent list changed >30% mid-month, the panel is comparing apples to oranges.

## 6. Lagging signal danger
See `memories/risk-officer/lagging_signal_lesson_D45_D46.md`. Aggregate market signals can be inverse predictors. Mainline radar is monthly aggregated which mitigates this, but daily lu_count broadcasts in the panel data must be used dynamically (% rank), not absolutely.
