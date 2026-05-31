# Technical Analysis Notes (etf-technical-analyst)

## ETF price-momentum context
Broad-market ETFs (CSI 300, CSI 500, SSE 50) mechanically track their index; price momentum
reflects macro regime, not stock-specific catalysts. Apply technical signals with lower conviction
than on individual equities.

## MA state rules
- Price > MA20 > MA60: uptrend — bullish (+1 to technical_score)
- Price < MA20 < MA60: downtrend — bearish (-1)
- MA20 crossing above MA60 (golden cross, within last 5 sessions): strong bullish (+2)
- MA20 crossing below MA60 (death cross, within last 5 sessions): strong bearish (-2)
- Mixed (price between MA20 and MA60, or MAs flat): neutral (0)

## RSI guidance
- RSI > 70: overbought; reduce score by 1 unless in confirmed strong uptrend
- RSI < 30: oversold; add +1 if macro context not deteriorating
- RSI 40–60: neutral zone; no adjustment

## Volume / liquidity caveat
ETF daily volume can spike on creation/redemption flows unrelated to price direction.
Confirm volume surge aligns with net premium/discount before treating as directional signal.
ADV < 5000万 CNY: flag as low-liquidity; cap technical_score at 0 (execution risk).

## Score range: -2..+2
Combine MA state (+2/-2/+1/-1/0) with RSI adjustment and liquidity cap.
Always report: ma_state, rsi value, ADV estimate, trend_signals list.
