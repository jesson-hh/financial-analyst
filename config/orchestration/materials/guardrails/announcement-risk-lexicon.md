# Announcement-risk lexicon guardrail (AMEND-8 §8.3 `dec.pm`) — 公告风险确定性词表

This guardrail binds the deterministic announcement-risk scanner that produces the
`AnnouncementRiskFlags` block. The scanner lives in code
(`guanlan_v2/orchestration/decision_inputs.py`); this material is the human-reviewable
single source for the tier vocabulary + 排除词 and must be kept in lock-step with the code
constants (a守护 test asserts every term here appears in the code lexicon and vice versa).

The 三层烈度 (three severity tiers), ordered 立案调查 > 问询函 > 关注函.

## Tier 1 — hard-veto candidates (must be addressed in the thesis even when bullish)
- 立案调查
- 退市风险
- 质押强平
- 重大爆雷
- 大额解禁

Any tier-1 match is a **hard veto**: it caps the `dec.pm` rating at Hold, must be named in
the investment thesis, and sets the severe-negative-event fact feeding the allowed-actions
adapter. Arguing around a tier-1 flag is a violation.

## Tier 2 — tempers conviction
- 问询函
- 商誉减值
- 减持计划
- 定增

## Tier 3 — context only
- 关注函

## 排除词 (exclusion words) — suppress false positives
A clarification / denial / non-involvement announcement that merely mentions a risk term is
NOT a risk event. If any exclusion token is present the whole announcement contributes no
flags:
- 澄清
- 不属实
- 不存在
- 不涉及
- 传闻

## Discipline
- The scan is deterministic: same announcement texts ⇒ identical flags (deduped by
  (tier, keyword), sorted by severity then keyword). It grants no authority and writes
  nothing; it only surfaces, before the LLM runs, the tiered risk flags and the hard-veto
  bit for the decision seats to honor.
