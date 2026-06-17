# D45/D46 Lagging Signal Lesson

## The problem
Aggregate market signals (limit-up count, big-dragon participation, "已涨多") are LAGGING. Their fwd_5d is INVERSE.

## What NOT to do
- Don't time the market by limit-up breadth
- Don't use big-dragon as confirmation (it's a contra signal)
- Don't trade off "rally is X days old"

## What TO do
- Use dynamic percentile windows
- Use monthly state classification (mainline / initiation / revival / decay / cold)
- Time scale: confirm the analysis time horizon BEFORE pulling data
- For multi-day setups, use cross-sectional rank, not absolute breadth
