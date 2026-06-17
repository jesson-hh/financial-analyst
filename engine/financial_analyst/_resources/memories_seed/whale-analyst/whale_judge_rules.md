# Whale Behavior Rules

## OBV trend
- 20-day slope > 0 → up (accumulation)
- slope < 0 → down (distribution)
- divergence with price: warning

## VR (Volume Ratio)
- > 2.0: strong (active accumulation)
- 0.5-2.0: neutral
- < 0.5: weak (distribution risk)

## MFI (Money Flow Index)
- > 80: overbought
- < 20: oversold
- divergence: reversal signal

## Lower shadow ratio (lower_shadow / body)
- > 1.5 sustained: support, smart money buying lows
- < 0.5: weak support

## Chip concentration
- VR>1.5 + OBV up: concentrated (good)
- VR<0.5 + OBV down: dispersed (bad)

## Aggregated whale_judge
- >=2 bullish (OBV up + VR strong + shadow support): "accumulating"
- VR weak + OBV down: "distributing"
- else: "neutral"
