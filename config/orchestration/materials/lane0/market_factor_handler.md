# lane0.market.factor.handler — deterministic market-factor worker handler

Trusted deterministic handler for the `market.factor` worker. The runtime binds
this catalog identity to `guanlan_v2.orchestration.market.factors.
market_factor_handler` — a pure delegation to `compute_market_factors` over
PIT-windowed `MarketFactorInputs` (Phase 5 Task 3): strictly point-in-time
(`available_at <= as_of` verified defensively, violations raise
`FutureDataRefused`), UNAVAILABLE never zero-filled, a backfilled tape snapshot
never a same-day observation. The emitted `MarketFactorReport@1` is its own
evidence (tool calls FORBIDDEN, no number anchors required).

These bytes PIN the reviewed factor set, so the catalog digest moves with the
battery — changing the factor set is a reviewed catalog change, never a silent
redefinition:

factor_set_version: mfs-v1
factor_set_digest: 40819483d104520aa6a3c25a7dfdc3c89a522e07c0cfe04fa9f70ce835d01ab7
