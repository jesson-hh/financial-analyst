# Revision-throttle guardrail (D6) — 修订节流

This guardrail is binding on the offline pattern curator (#27) and any proposal lane
that can revise a dictionary pattern or a factor battery. It binds the D6 throttle
VOCABULARY and discipline only — it never re-implements the admission rule.

The throttle vocabulary
- 日频形态 (daily-cadence K-line patterns): a given pattern may be revised at most once
  per N = 20 交易日 (trading days).
- 月频因子 (monthly-cadence factors): N = 3 (three cadence periods).
- A proposal that would revise the same pattern / factor again inside its throttle
  window is not admitted; it waits.

Where the rule actually lives (never re-implemented here)
- The throttle ADMISSION rule itself is the Phase-4 governor primitive; this material
  only states the reviewed N values and the discipline.
- The maturity of the observation window (是否已积累足够成熟样本) is measured by the
  Phase-5 matured-case grader — the same counting source the miner/curator path uses.
  A revision is never admitted on an immature observation window (样本不足绝不硬给).

Discipline
- Revision is throttled AND advisory: passing the throttle admits a proposal for human
  review, it never auto-applies a change. Adoption is always 人审.

Scope
- This guardrail governs proposal cadence. It grants no authority to act and it writes
  nothing; it only bounds how often a revision may be proposed and defers the counting
  to the governor + matured-case grader.
