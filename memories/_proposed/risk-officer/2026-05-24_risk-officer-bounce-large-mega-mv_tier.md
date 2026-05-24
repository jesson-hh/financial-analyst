---
topic: risk-officer-bounce-large-mega-mv_tier
title: Auto-flag 'mega_cap_factor_bounce_invalid' when cross-sectional factor signals
  suggest long bounce on mega-cap utility s
target_agent: risk-officer
confidence: med
generated_at: '2026-05-24'
supporting_cases:
- 2026-05-23_SH600009
- 2026-05-23_SH600036
- 2026-05-23_SH600438
- 2026-05-23_SH600519
reasoning: Aggregated from 4 pending introspections via Jaccard clustering (threshold=0.4).
  All in risk-officer bucket.
---

# risk-officer — aggregated rule proposal

Auto-aggregated from **4 pending introspections** (cluster threshold 0.4, 
Jaccard with BOOST_KEYWORDS 2x).

## Cluster member proposals

### 1. case `2026-05-23_SH600009` (confidence=med)

**Pattern**: mv_tier='large'/'mega' + defensive/utility sector + technical mean-reversion signal (RSI/rev bounce)

**Proposed rule**: Auto-flag 'mega_cap_factor_bounce_invalid' when cross-sectional factor signals suggest long bounce on mega-cap utility stocks. Factor IC decay neutralizes technical bounces; downgrade to neutral/avoid and enforce position_pct=0%.

**Rationale**: Matches quant bear's explicit note on IC degradation and risk officer's veto. Large-cap utilities are driven by fundamentals/traffic, not short-term factor bounces. Adding this to CRO rules prevents false long signals.

### 2. case `2026-05-23_SH600036` (confidence=low)

**Pattern**: mv_tier == 'large' AND vol_regime_label == 'neutral' AND turnover_60 < 0.05

**Proposed rule**: Flag 'shrinking volume + oversold RSI' as liquidity trap/lack of interest rather than selling exhaustion. CRO must veto bull theses relying solely on technical bounces without volume expansion or institutional flow confirmation.

**Rationale**: In this report, bull misread turnover_60=0.035 as stabilization, while CRO correctly identified it as liquidity drought causing fake bounces. Large-cap defensive stocks frequently exhibit this pattern; treating low turnover as bullish is a systematic error.

### 3. case `2026-05-23_SH600438` (confidence=low)

**Pattern**: mv_tier in ['mid', 'large'] AND sector_cycle in ['capacity_clearing', 'earnings_trough'] AND quant_model_consensus == 'strong_long' driven by reversal_factors (rsi<30, rev_20>0, close_to_high_20<-10%)

**Proposed rule**: CRO must auto-flag 'mega_cap_factor_bounce_invalid' and enforce position_sizing=0%. Bull thesis citing V4/V1 oversold bounce must be downgraded to 'technical dead cat' unless explicit capacity-clearing catalyst timeline or fundamental inflection is present. Writer action must escalate to 'avoid' when CRO veto triggers.

**Rationale**: Single-stock post-mortem shows risk-officer correctly identified that reversal-factor-driven quant consensus fails for mid/large-cap stocks in sector downtrends. This feature intersection (mv_tier + sector_cycle + quant_consensus) reliably kills oversold bounce theses. Adding a CRO rule prevents bull-advocate from over-indexing on technical mean-reversion and aligns writer output with risk constraints.

### 4. case `2026-05-23_SH600519` (confidence=med)

**Pattern**: mv_tier in ['large', 'mega'] AND bull cites bounce/oversold factors (rev_20, vol_20, RSI) AND board_total_score is null

**Proposed rule**: Auto-flag bull's factor-based bounce thesis as structurally invalid due to IC decay. Require explicit V3 sector regime confirmation; if sector is cold/decay, force position_sizing to 0% and action to 'avoid'.

**Rationale**: Strongly aligns with existing memory rule 'large_cap_factor_neutralized'. Mega-cap bounces without sector resonance consistently fail. CRO veto on factor bounces for large caps prevents false alpha signals and overrides writer's tendency to overweight short-term technicals.

---

## Recommendation

Above 4 cases all point to the same pattern under `risk-officer`.
Review the proposed rules + pick the cleanest formulation. After manually crafting the final rule, run `fa dream accept risk-officer/<slug>` to promote into permanent memory.

If false-pattern (e.g., all from one stock or one date), reject with
`fa dream reject risk-officer/<slug>`.
