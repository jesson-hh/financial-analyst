# Holdings Scoring Rules (etf-holdings-analyst)

## Concentration scoring (holdings_score: -2..+2)

### HHI thresholds (top-10 holdings Herfindahl-Hirschman Index)
- HHI > 0.25 (highly concentrated): -2 — single-sector or mega-cap dominance; tracking risk elevated
- HHI 0.15–0.25 (moderate concentration): -1 — limited diversification benefit
- HHI 0.08–0.15 (normal broad index): 0 — neutral; typical CSI 300/500 range
- HHI 0.05–0.08 (well-diversified): +1 — cross-sector breadth; factor dilution benefit
- HHI < 0.05 (highly diversified): +2 — deep breadth, near-equal weight

### Top-holding weight (single stock)
- Top-1 weight > 15%: apply -1 penalty (stacks with HHI score, floor -2)
- Top-1 weight > 10%: apply -0.5 (round down)
- Top-3 cumulative > 40%: apply -1 penalty

### Single-stock risk flag
- Any single holding with weight > 20%: set holdings_score = -2 unconditionally (extreme concentration)

### Index breadth bonus
- Underlying index constituents ≥ 500: +1 bonus (capped at +2 total)
- Underlying index constituents ≥ 300: +0 (no bonus/penalty)
- Underlying index constituents < 100: -1 penalty (niche/thematic; liquidity tail risk)

## Score derivation guidance
Start from HHI base score, apply top-holding and breadth adjustments, clamp to [-2, +2].
Document top-3 holdings, their weights, primary sector, and HHI in output for downstream agents.
