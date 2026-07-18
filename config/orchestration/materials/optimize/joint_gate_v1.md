# optimize.joint_gate v1 — reviewed research joint-gate semantics

`kind = gate_metric` · `id = optimize.joint_gate` · `version = 1`

This material documents the frozen research validation gate the Phase 4
Evaluator-Optimizer applies to a candidate's L1 `ValidationMetrics` before it may
progress. It is reviewed catalog documentation only — it carries no executable
authority and grants no trading power; the gate itself is the deterministic
`GateResult` computed by the optimizer.

## The three-way joint predicate

A candidate `passes` the research gate only when **all three** hold together:

1. `rank_ic >= min_rank_ic` — the rank information coefficient clears the caller's
   reviewed floor (NaN is never treated as passing);
2. `oos_verdict == "robust"` — the out-of-sample decay verdict is exactly
   `"robust"` (`degraded` / `overfit` / `insufficient` / `na` all fail);
3. `sharpe > 0` — the strategy Sharpe ratio is strictly positive.

Any missing (honestly `None`) input is a fail, never a silent pass. This mirrors
the existing factor-research loop gate (`guanlan_v2/research/loop.py`
`_gate`): `passed`, `min_rank_ic`, `oos_required = "robust"`,
`sharpe_required = True`.

## Honesty red lines

- Absent metrics stay `None` and never zero-fill a passing value.
- The gate ranks nothing and reweights nothing — it is a pure admission predicate.
- A holdout (sealed) evaluation never reaches this gate; only validation-stage
  metrics are gated here.
