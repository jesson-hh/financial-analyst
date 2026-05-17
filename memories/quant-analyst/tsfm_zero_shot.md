# TSFM Zero-Shot Closure (n=5)

## Tested (all RankICIR <= 0)
- Chronos, TimesFM, Moirai2, TimeMoE

## Only winner
- FinCast: +0.386

## Production
- B3 (LGB60 + FC40 weighted): +1.083 RankICIR
- B3v2 single-side adaptive: FC degrades to ICIR=-0.172 → auto-shift w_FC from 0.40 to 0.17

## Lessons
- Generic TSFM does not transfer to A-share zero-shot
- Finance-specific pretraining (FinCast) is necessary
- Walk-forward weight adaptation crucial for B3 stability
