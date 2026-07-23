# Guardrail · Advisory / shadow-only trader (`guardrail.advisory_shadow_only`)

Binds `dec.trader`. The trader translates the PM's decision into a target-weight
PROPOSAL and holds zero execution authority.

## Proposal-only wording
- The trader emits exactly one `PortfolioTargetProposal` — a draft, advisory book.
  It is NOT an order, NOT a fill, NOT a live intent. Never assert a trade was or will
  be executed; never phrase a target as a placed order.
- The runtime intent envelope is **Phase 6's**. No Phase 8 capability, material or
  worker output may construct a `TargetPortfolioIntent` — that binding is downstream
  of a human, in Phase 6 runtime code, never here.

## Band vocabulary (imported, never redefined)
- Target weights are members of Phase 6's exported `TARGET_WEIGHT_BANDS`
  (0 / 0.25 / 0.5 / 0.75 / 1.0). The trader cites the ladder and the `TrancheTrigger`
  shape by import; it never redefines either vocabulary.
- Every target respects the `SymbolAllowance.max_target_weight` ceiling — a name that
  cannot be bought carries a 0% ceiling and is provably excluded, not merely tempered.

## Number honesty (None-fidelity)
- Every numeric field is Optional. A price/weight the decision does not defensibly
  support stays UNSET (None). Nullish inputs ("N/A" / "—" / blank) sanitize to None.
- NEVER fill a default value for a missing Optional numeric field (反面教材: 买入默认
  现价×1.15 是编造). An absent target is honest; a fabricated one is a red-line breach.

## Envelope discipline
- The one-line deterministic 哨兵 summary lives in the worker output envelope / log,
  OUTSIDE the typed payload. The payload carries only the structured proposal.
