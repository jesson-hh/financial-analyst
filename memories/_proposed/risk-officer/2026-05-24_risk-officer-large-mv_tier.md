---
topic: risk-officer-large-mv_tier
title: 'When mv_tier is large and conviction is low, automatically trigger hard_rule_triggers
  for ''factor_decay_large_cap'', set '
target_agent: risk-officer
confidence: med
generated_at: '2026-05-24'
supporting_cases:
- 2026-05-23_SH600030
- 2026-05-23_SH600276
- 2026-05-23_SH600900
- 2026-05-23_SH601288
- 2026-05-23_SZ000333
reasoning: Aggregated from 5 pending introspections via Jaccard clustering (threshold=0.4).
  All in risk-officer bucket.
---

# risk-officer — aggregated rule proposal

Auto-aggregated from **5 pending introspections** (cluster threshold 0.4, 
Jaccard with BOOST_KEYWORDS 2x).

## Cluster member proposals

### 1. case `2026-05-23_SH600030` (confidence=med)

**Pattern**: mv_tier == 'large' AND quant_score > 0 AND conviction_level == 'low'

**Proposed rule**: When mv_tier is large and conviction is low, automatically trigger hard_rule_triggers for 'factor_decay_large_cap', set position_sizing_advice to '0%', and require explicit macro beta catalyst for any non-zero position. Do not rely on quant consensus alone.

**Rationale**: Bull advocates consistently over-index on quant scores for large caps despite known structural decay. Risk-officer must enforce the decay rule as a hard veto trigger to prevent false buy signals. Aligns with existing memory rule on large-cap factor decay.

### 2. case `2026-05-23_SH600276` (confidence=low)

**Pattern**: mv_tier == large AND (valuation_missing OR quant_snapshot_stale)

**Proposed rule**: 当大市值标的缺乏估值数据或量化快照过期时，CRO 必须强制校验 rating_overall 与 sum(rating_dimensions) 的算术一致性。若不一致，自动降级 conviction_level 为 low，并在 conditional_approval 中注明数据缺失对评分的压制效应，禁止 writer 单方面覆盖维度分。

**Rationale**: 本报告同时触发大市值因子归零、估值缺失与量化快照过期(17个月)，导致各维度评分失真且 writer 未对齐总分。CRO 增加一致性校验防线可防止隐性评分偏移，符合'加防线优先'原则，且不影响 bull-advocate 的乐观空间。

### 3. case `2026-05-23_SH600900` (confidence=med)

**Pattern**: mv_tier == 'large' AND fundamental.valuation_score == 0 (PE/PB missing)

**Proposed rule**: 自动触发“估值锚定失效”盲点检查，强制要求补充股息率分位数/北向持仓/ETF申赎等替代验证指标，并将 position_sizing_advice 上限收紧至 1-2%。

**Rationale**: 大盘蓝筹在缺乏基本面定价锚时，技术面多头排列易被误读为安全垫。CRO增加此规则可防止在无估值支撑时过度依赖量价信号，符合V10执行纪律与防御定位，且CRO veto 权能安全拦截潜在回撤。

### 4. case `2026-05-23_SH601288` (confidence=low)

**Pattern**: mv_tier == 'large' AND fundamental valuation data missing (PE/PB/PS null)

**Proposed rule**: 自动标记'因子ICIR衰减'风险，强制要求补充基本面定价锚或降级rating至avoid/hold，禁止依赖纯技术/量化因子做买入建议。

**Rationale**: 本例大盘银行股因子预测力衰减，技术面超卖信号在缺乏基本面锚定时易失效。CRO加此防线可防止量化/技术agent在大盘股上过度交易。

### 5. case `2026-05-23_SZ000333` (confidence=low)

**Pattern**: mv_tier=large AND valuation_metrics_null AND technical_score>0 AND quant_score<=0

**Proposed rule**: 当大盘股缺失PE/PB等核心估值数据时，CRO必须强制触发veto_flags=['fundamental_anchor_missing']，将action覆写为'avoid'，并将target_price置空。技术面信号不得单独作为大盘股买入/持有依据。

**Rationale**: 本单显示写手在基本面数据缺失时仍保留看多目标价(85.0)，却给出卖出动作，导致输出自相矛盾。挂接CRO veto可切断技术面单腿逻辑，符合V4/V6对大盘股基本面安全垫的要求，且比削弱分析师更安全。

---

## Recommendation

Above 5 cases all point to the same pattern under `risk-officer`.
Review the proposed rules + pick the cleanest formulation. After manually crafting the final rule, run `fa dream accept risk-officer/<slug>` to promote into permanent memory.

If false-pattern (e.g., all from one stock or one date), reject with
`fa dream reject risk-officer/<slug>`.
