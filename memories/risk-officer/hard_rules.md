# Hard Rules (cannot be overridden by analyst opinion)

## Veto 1: game-capital ticker
**Trigger:** mv<200亿 AND pe>100 AND ret60>50%
**Action:** veto_flags += ["game_capital_speculation"]; position_pct = 0
**Reason:** quant models structurally unreliable for these names

## Veto 2: negative event severity>=2 within 7 days
**Trigger:** any event in `event_classified.negative` with severity>=2
**Action:** veto_flags += ["recent_severe_negative_event"]; position_pct = 0 (if not held), trigger sell (if held)
**Reason:** R25 negative event hard-sell rule

## Veto 3: super_distr regime
**Trigger:** vol_regime.regime_label == "super_distr"
**Action:** veto_flags += ["super_distribution_active"]; reduce position to 0.5x max
**Reason:** R14 SS-grade signal, fwd_5d -4.20pp

## Veto 4: 5-bar break on first board
**Trigger:** board_score.detail.seal_at_close == False AND board_score.v5_score < 0
**Action:** if considering entry, veto

## Position sizing rules
- veto active: position_pct = 0
- all bullish + no veto: 3-5%
- mixed + no veto: 1-3%
- bearish dominant: 0%
