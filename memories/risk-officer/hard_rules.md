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

## Veto 5: large-cap factor bounce structurally invalid
**Trigger:** mv_tier in ["large", "mega"] (即 total_mv > 1000亿)
           AND bull.thesis_bullets 引用因子 bounce 论据
               (RSI<30 oversold / rev_20 / vol_20 / vol_regime=bounce 任一)
           AND whale.board_total_score is None (无涨停板共振)
           AND fundamental.mv_tier 已触发 large_cap_factor_neutralized 红牌
**Action:** veto_flags += ["mega_cap_factor_bounce_invalid"];
           position_pct = 0; action = 'avoid' (或 'sell' 若已持仓);
           override bull short-term technical claims.
**Reason:** 大盘股 (>1000亿) 因子 IC 衰减后实际为负, bull 即使引用 RSI/rev_20
           等"超跌反弹"指标也是结构性失效信号. 14/14 大盘报告样本 (2026-05-23 batch)
           introspector 反复指出此 pattern, 与 CLAUDE.md 既有规则
           "大盘股因子面强制归零" 一致.
**Evidence:** memories/_pending_introspections/2026-05-23_SH*.json (14 份),
            target_agent="risk-officer", 关键词 mv_tier+large 命中 64% 提案.
**Override condition:** 只有 V3 板块状态明确为 mainline / initiation
           (R27 broadcast=positive) 才允许放宽到 position_pct ≤ 1%.
**Anti-pattern:** "大盘股 RSI 27, 超跌反弹机会" → 这种 thesis 必须 veto, 不论
            bull 多么自信. 触发判定: bull 文本含 "超跌" / "反弹" / "回踩到位" 但
            mv_tier 是 large/mega.

## Position sizing rules
- veto active: position_pct = 0
- all bullish + no veto: 3-5%
- mixed + no veto: 1-3%
- bearish dominant: 0%
