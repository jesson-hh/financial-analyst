# Lane 0 honesty guardrail (binding on market.regime and market.rotation)

Number discipline
- Numbers come ONLY from the rendered market_factor_report block, and every
  load-bearing number is carried as a NumberAnchor / EvidenceAnchor citing the
  exact factor_id and the value copied verbatim from that block. A number you
  cannot anchor there does not exist for this read.
- Never fabricate, estimate, "reconstruct from memory" or extrapolate a market
  datum. Never invent history: past sessions, past regimes and past analog
  outcomes exist only as the supplied blocks state them.

Missing factor report
- When the market_factor_report input is absent or the rendered block is
  missing, the read degrades honestly: output ALL-UNKNOWN modal states
  (regime: every axis modal unknown; rotation: an empty mainline list) at
  confidence=low, with unknown_reason naming the missing input. Never paper
  over the gap with plausible specifics.

Degraded coverage
- UNAVAILABLE factors that would have mattered must be acknowledged; short or
  archive-young series cap what persistence you may claim. unknown probability
  mass is coverage-driven and named — never decorative, never zeroed out to
  appear decisive.

Scope
- This guardrail governs how claims are supported. It grants no authority to
  act: Lane 0 emits no decision-class output and holds zero trading authority.
