# Orchestration Phase 1 · 24-Worker Ownership Map (Task 0 evidence)

> Every design worker ID appears exactly once. `status` ∈ `planned | contract_ready | runnable`.
> Phase 1 (Task 0) freezes **zero** runnable WorkerSpecs: every row is `planned`. A `planned`
> row is not a runnable WorkerSpec. `compat.*` legacy-preservation IDs (see the graph table in
> the contract map) never satisfy or replace one of these 24 final IDs.

| worker_id | lane | execution_kind | legacy_owner/source | legacy_config_schema/digest | proposed output ABI | can_emit_decision | status |
|---|---|---|---|---|---|---|---|
| `market.factor` | market | deterministic | guanlan regime factor family + market_tape (帷幄) (`guanlan_v2/strategy/compute/factor_regime.py`) | guanlan_v2.strategy.compute.factor_regime@1 | market_factor_report (factor trend vector + coverage) | False | planned |
| `market.regime` | market | llm | NEW LLM regime read over market.factor + experience library (帷幄+新) | — | regime_report (trend x risk x heat, structured + prob/confidence) | False | planned |
| `market.rotation` | market | llm | guanlan fundflow/industry mainline rotation (帷幄+新) (`guanlan_v2/fundflow/pulse.py`) | — | rotation_report (mainline ranking + 启动/扩散/分化/退潮 stage) | False | planned |
| `quant.factor` | quant | deterministic | guanlan rescore/factor_ic (帷幄) (`guanlan_v2/screen/factor_ic.py`) | guanlan_v2.screen.factor_ic@1 | factor IC report | False | planned |
| `quant.model` | quant | deterministic | guanlan v4 + DL ensemble (帷幄) (`guanlan_v2/screen/model_registry.py`) | guanlan_v2.screen.model_registry@1 | model prediction report | False | planned |
| `quant.backtest` | quant | deterministic | guanlan backtest cards (vintage/OOS/PBO) (帷幄) (`guanlan_v2/screen/factor_vintage.py`) | guanlan_v2.screen.factor_vintage@1 | backtest report (vintage IC / OOS / PBO) | False | planned |
| `quant.fundamentals` | quant | deterministic | TA fundamental-analyst + astock get_profit_forecast (`engine/financial_analyst/agent/tier2/fundamental_analyst.py`) | — | fundamentals report (valuation_score, mv_tier) | False | planned |
| `quant.factor_miner` | quant | deterministic | guanlan research/loop (Sharpe/robust gate) (帷幄) (`guanlan_v2/research/loop.py`) | — | mined factor draft (passes Sharpe/robust gate) | False | planned |
| `pv.price_action` | pv | deterministic | guanlan 15-key PA + editable methodology (帷幄 EV-017~026) (`guanlan_v2/seats/price_action.py`) | guanlan_v2.seats.price_action@1 | price-action features (15 keys) + methodology read | False | planned |
| `pv.technical` | pv | llm | TA technical-analyst + get_verified_snapshot truth anchor (`engine/financial_analyst/agent/tier2/technical_analyst.py`) | — | technical report (<=8 complementary indicators) | False | planned |
| `pv.microstructure` | pv | deterministic | guanlan live_book/market_tape/fundflow (五档/逐笔/炸板/主力) (帷幄) (`guanlan_v2/seats/live_book.py`) | guanlan_v2.seats.live_book@1 | microstructure report (L1 book / ticks / break / whale) | False | planned |
| `text.news` | text | llm | guanlan kuaixun/news_marks (~TA) (`guanlan_v2/datafeed/kuaixun.py`) | guanlan_v2.datafeed.kuaixun@1 | news report (flash + global) | False | planned |
| `text.sentiment` | text | llm | TA anti-fabrication #557/#796 + guanlan sentiment (no tools, pre-fetched blocks) (`guanlan_v2/datafeed/sentiment.py`) | guanlan_v2.datafeed.sentiment@1 | SentimentReport (band/score/confidence) | False | planned |
| `text.research_report` | text | llm | guanlan Kimi research extraction + old-report downweight (帷幄) | — | research-report extraction (+ stale-report downweight) | False | planned |
| `text.policy` | text | llm | astock policy / window guidance (NEW) | — | policy report (policy / window guidance) | False | planned |
| `text.macro` | text | llm | guanlan macro + TA get_prediction_markets (prediction markets + limit-up temp) (`guanlan_v2/macro/pulse.py`) | guanlan_v2.macro.pulse@1 | macro report (prediction markets + board temperature) | False | planned |
| `dec.bull` | decision | llm | TA bull-advocate (tier3) (`engine/financial_analyst/agent/tier3/bull_advocate.py`) | — | bull thesis_bullets [V#] + target_price_high/base | False | planned |
| `dec.bear` | decision | llm | TA bear-advocate (tier3), rebuttal wave (`engine/financial_analyst/agent/tier3/bear_advocate.py`) | — | bear thesis_bullets [F#] + target_price_low/downside | False | planned |
| `dec.research_mgr` | decision | llm | TA report-writer (ReportOutput -> ResearchPlan 5-band) (`engine/financial_analyst/agent/tier3/report_writer.py`) | financial_analyst.agent.tier3.report_writer.ReportOutput@1 | ResearchPlan (5-band PortfolioRating + strategic actions) | True | planned |
| `dec.risk_debate` | decision | llm | TA risk-officer (aggressive/steady/neutral 3-seat debate) (`engine/financial_analyst/agent/tier3/risk_officer.py`) | — | risk_score, veto_flags, position_sizing_advice | False | planned |
| `dec.pm` | decision | llm | NEW A-share-constraint final arbitration (PortfolioDecision, deep tier) | — | PortfolioDecision (final rating + thesis + optional target) | True | planned |
| `dec.trader` | decision | llm | NEW trader (PortfolioTargetProposal; runtime wraps ADVISORY_ONLY+SHADOW_ONLY intent) | — | PortfolioTargetProposal (shadow-only) | True | planned |
| `x.quality_gate` | cross | deterministic | astock data-quality ABCDF grade (`guanlan_v2/datafeed/health.py`) | guanlan_v2.datafeed.health@1 | data quality grade (A/B/C/D/F) | False | planned |
| `x.number_critic` | cross | deterministic | guanlan introspector provenance gate + FSI untrusted-input isolation (帷幄) (`engine/financial_analyst/agent/tier3/introspector.py`) | financial_analyst.agent.tier3.introspector.IntrospectionProposal@1 | provenance violations ([UNSOURCED] on unsourced load-bearing numbers) | False | planned |

