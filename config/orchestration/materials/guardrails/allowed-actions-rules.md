# Allowed-actions guardrail (AMEND-8 §8.1 第 1 条) — 硬约束前置

This guardrail binds the deterministic pre-input adapter that computes the `AllowedActions`
block **before** `dec.pm` and `dec.risk_debate`. It states the discipline only; the rule
itself lives in code (`guanlan_v2/orchestration/decision_inputs.py`), never re-implemented
here. It is the ai-hedge-fund `risk_manager` precedent: the hard-constraint layer is NOT
the LLM.

## The block is already validated
- Per symbol the block carries: 可否买卖 (`can_buy` / `can_sell`), 手数 (`lot_size`), and
  the 最大目标仓位带 (`max_target_weight`).
- The block is marked **already validated**. The LLM only SELECTS within the allowed set —
  it never recomputes a constraint, never does the arithmetic, and never argues an excluded
  action back in. A great thesis on an untradable or limit-locked name is not a Buy today.

## The maximum target-weight band vocabulary is Phase 6's, imported
- `max_target_weight` is always a member of the frozen `TARGET_WEIGHT_BANDS`
  (0 / 25 / 50 / 75 / 100%) exported by Phase 6 — imported by object identity, never a
  local copy. An allowance can never carry a weight outside that closed vocabulary.

## The A-share 制度 constraints computed (deterministic, from real data surfaces)
- **T+1 settlement**: shares bought today are not salable until the next trading day —
  a today-acquired holding sets `can_sell=False`.
- **涨跌停 一字板** (sealed limit boards): a sealed 一字涨停 cannot be bought (`can_buy=False`,
  ceiling 0) though a holder may still sell; a sealed 一字跌停 cannot be sold
  (`can_sell=False`). A non-sealed touch is still tradable.
- **停牌** (suspension): neither side trades — `can_buy=False`, `can_sell=False`, ceiling 0.
- **手数** (board lots): 100 shares, STAR (科创板) minimum 200.
- **ST / 退市风险**: reduced exposure — the target ceiling is capped to the 25% band.

## The CRO hard rules computed (不可被观点推翻)
- **游资票否决**: 市值 < 200 亿 ∧ PE > 100 ∧ 60 日涨幅 > 50% → `can_buy=False`, ceiling 0.
  All three conditions are required; a missing input never fabricates the veto.
- **恶性事件否决**: a severe negative event (sourced from the tier-1 announcement-risk hard
  veto) → `can_buy=False`, ceiling 0.

## Honesty red line
- A name that cannot be bought carries a zero target ceiling — the impossible action is
  provably EXCLUDED from the allowed set, not merely discouraged. `dec.risk_debate` argues
  aggressiveness / timing / sizing WITHIN the allowed set; it never re-litigates the hard
  rules (they are already pre-computed here).

## Scope
- This adapter grants no authority to act and writes nothing. It only bounds, before the
  LLM runs, what each name's actionable set is; the debate seats and the PM select within it.
