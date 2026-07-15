# Orchestration Phase 1 · Legacy Contract Map (Task 0 evidence)

> Human-reviewed. One row per real legacy scalar field (verbatim from source; no summaries).
> `mapping_basis` ∈ `authoritative_code | approved_policy | none`. `roundtrip_policy=exact`
> is used ONLY when the raw value set equals the target value set (lossless identity).
> Where no authoritative mapping exists the target is `UNMAPPABLE` with an explicit reason;
> no inferred mapping is invented. The completeness test cross-checks every fixture scalar's
> `(source_schema, semantic_domain)` pair against this table.

| source_schema | source_path | field/node | raw_type/domain | semantic_domain | target_schema | adapter/policy_version | mapping_basis | evidence | roundtrip_policy | unmapped_policy |
|---|---|---|---|---|---|---|---|---|---|---|
| financial_analyst.agent.tier3.report_writer.ReportOutput@1 | engine/financial_analyst/agent/tier3/report_writer.py | action | str · buy, hold, sell, avoid, accumulate | research_recommendation | guanlan_v2.orchestration.enums.ResearchAction@1 | v1 | authoritative_code | report_writer.py:263 valid_actions={buy,hold,sell,avoid,accumulate}; model_validator hard-guard | exact | — |
| financial_analyst.agent.etf.report_writer.EtfReportOutput@1 | engine/financial_analyst/agent/etf/report_writer.py | action | str · buy, hold, sell, avoid, accumulate | research_recommendation | guanlan_v2.orchestration.enums.ResearchAction@1 | v1 | authoritative_code | etf/report_writer.py:58-59 valid_actions={buy,hold,sell,avoid,accumulate}; SEPARATE class from stock ReportOutput | exact | — |
| financial_analyst.backtest.decision.DecisionLeg@1 | engine/financial_analyst/backtest/decision.py | action | str · buy, add, hold, reduce, sell | position_adjustment | guanlan_v2.orchestration.enums.PositionAction@1 | v1 | authoritative_code | decision.py:32 _VALID_ACTIONS={buy,add,hold,reduce,sell}; decision.py:253 unknown->hold | exact | — |
| financial_analyst.watch.models.WatchRec@1 | engine/financial_analyst/watch/models.py | action | str · buy, add, hold, reduce, sell | position_adjustment | guanlan_v2.orchestration.enums.PositionAction@1 | v1 | authoritative_code | watch/models.py:6 _ACTIONS={buy,add,hold,reduce,sell}; __post_init__ raises on bad action | exact | — |
| financial_analyst.agent.tier3.report_writer.ReportOutput@1 | engine/financial_analyst/agent/tier3/report_writer.py | rating_overall | int · [-10,10] | research_rating_score | UNMAPPABLE | v1 | none | report_writer.py:254 Field(ge=-10, le=10); strict int | preserve_raw | int score [-10,10] has no authoritative binning to the 5-band PortfolioRating; preserve raw int |
| financial_analyst.agent.etf.report_writer.EtfReportOutput@1 | engine/financial_analyst/agent/etf/report_writer.py | rating_overall | int · [-10,10] | etf_rating_score | UNMAPPABLE | v1 | none | etf/report_writer.py:47 Field(ge=-10, le=10) = sum of 5 dims (each -2..+2, risk -2..0) | preserve_raw | ETF int score [-10,10]; SEPARATE composition from stock; no authoritative band binning; preserve raw int |
| financial_analyst.agent.tier3.introspector.IntrospectionProposal@1 | engine/financial_analyst/agent/tier3/introspector.py | confidence | str · low, med, high | introspection_confidence | UNMAPPABLE | v1 | none | introspector.py:32 confidence='low'/'med'/'high'; introspector.py:50-51 2=low/3-5=med/6+=high | preserve_raw | raw 'med' != design Confidence 'medium'; med->medium rename has no authoritative code or approved policy yet; preserve raw |
| guanlan_v2.strategy.perspectives.market_cycle@1 | guanlan_v2/strategy/perspectives.py | stage | str · 冰点, 分化, 逼空, 发酵, 回踩/启动 | market_cycle_stage | guanlan_v2.orchestration.enums.LegacyMarketCycleStage@1 | v1 | authoritative_code | perspectives.py:65-74 lu<0.10 冰点 / lu>=0.90&amt>=0.90 分化 / lu>=0.70 逼空 / lu>=0.35 发酵 / else 回踩/启动 | exact | — |
| financial_analyst.agent.tier1.news_sentiment.NewsSentimentOutput@1 | engine/financial_analyst/agent/tier1/news_sentiment.py | market_tilt | str/null · 利好, 利空, 中性 | news_market_tilt | UNMAPPABLE | v1 | none | news_sentiment.py:17 market_tilt Optional[str] 利好/利空/中性; news_pulse.py:22 NEWS_SYSTEM vocab | preserve_raw | 3-level 利好/利空/中性 (nullable) has no authoritative mapping to 6-level SentimentBand; preserve raw |
| financial_analyst.agent.tier1.news_sentiment.NewsSentimentOutput@1 | engine/financial_analyst/agent/tier1/news_sentiment.py | stock_tilt | str/null · 利好, 利空, 中性 | news_stock_tilt | UNMAPPABLE | v1 | none | news_sentiment.py:18 stock_tilt Optional[str]; None when no relevant flash | preserve_raw | per-stock 3-level tilt (nullable, None=no coverage); no authoritative 3->6 band mapping; preserve raw |
| guanlan_v2.datafeed.sentiment._TAG_SCORE@1 | guanlan_v2/datafeed/sentiment.py | score | float · -1.0, 0.0, 1.0 | news_tag_score | UNMAPPABLE | v1 | none | sentiment.py:20 _TAG_SCORE={利好:1.0, 中性:0.0, 利空:-1.0}; authoritative tag->score within legacy | preserve_raw | ternary {-1,0,1} store score; no authoritative rescale to design SentimentReport score[0,10]; preserve raw |
| guanlan_v2.macro.astock.build_astock@1 | guanlan_v2/macro/astock.py | temp | float · [0,100] | board_limitup_temperature | UNMAPPABLE | v1 | none | astock.py:73-76 temp=clamp(base + k_zt*zt_count + k_streak*max_streak - k_break*break_ratio, 0, 100); themes.yaml astock consts | preserve_raw | deterministic limit-up temperature [0,100]; no authoritative mapping to design SentimentReport score[0,10]; preserve raw |
| guanlan_v2.macro.pulse._theme_temp@1 | guanlan_v2/macro/pulse.py | temp | float · [0,100] | predictionmkt_temperature | UNMAPPABLE | v1 | none | pulse.py:59 temp=clamp(50 + 50*acc/tot_w, 0, 100); prediction-market anchored (PM/Kalshi prob-0.5 weighted) | preserve_raw | prediction-market anchored temperature [0,100]; display-only; no authoritative target scale; preserve raw |
| guanlan_v2.screen.market_temp._gate@1 | guanlan_v2/screen/market_temp.py | level | str/null · risk_off, overheat, neutral | market_temp_gate_level | UNMAPPABLE | v1 | none | market_temp.py:49-58 risk_off if temp<=25 or main_net<=-300; overheat if temp>=85 & break_rate>=0.35; else neutral; None=both inputs missing | preserve_raw | conservative shield gate {risk_off,overheat,neutral} (None=data insufficient); no design target enum; preserve raw |

## Legacy static graph — `stock-deep-dive.yaml`

- **source_schema**: `financial_analyst.swarm.stock-deep-dive@1`  
- **source_path**: `engine/financial_analyst/_resources/config/swarm/stock-deep-dive.yaml` (yaml)  
- **normalized_config_digest**: `5a175cb91de0cff8358da93272a4ac26fb29013a833a062a096fae56be5b9dbb` (sha256 of canonical node→{deps,soft_deps,input_keys})  
- **runnable**: `false` — Phase 1 freezes zero runnable WorkerSpecs; all node→worker links below are design-intent, not 1:1 equivalences.

**Dependency semantics (from `agent/orchestrator.py`):**

- **hard** (dep in `deps` and NOT in `soft_deps`): accepted = `completed_ok`; missing → orchestrator._ready: dep must be in `done` AND done[dep].ok; a failed/missing hard dep blocks the node forever -> downstream report dies (整份研报夭折)
- **soft** (dep listed in `soft_deps` (soft_deps subset of deps)): accepted = `completed_ok, any_terminal_done_regardless_of_ok`; missing → orchestrator._ready requires dep in `done` (must have run) but NOT ok; _build_inputs injects done[k].output only when done[k].ok, so a failed soft dep is omitted and the node runs degraded

| node | hard_deps | soft_deps | output slot meaning | design-intent worker |
|---|---|---|---|---|
| quote-fetcher | — | — | OHLC/quote/valuation fetch | compat.quote_fetcher |
| news-reader | — | — | raw stock news fetch | compat.news_reader |
| f10-reader | — | — | F10 holder-count / chip concentration | compat.f10_reader |
| market-scanner | — | — | market regime + limit-up breadth | market.regime |
| mainline-classifier | — | — | industry-chain mainline 5-state | market.rotation |
| morning-brief-writer | — | — | overnight moves + pre-open watchlist | compat.morning_brief |
| overseas-market-scanner | — | — | overseas macro (US/HK/VIX overnight) | text.macro |
| sector-rotation-analyzer | — | — | sector rotation | market.rotation |
| news-sentiment | — | — | market_read + per-stock tilt tag | text.sentiment |
| evidence-loader | — | — | platform evidence pack (deterministic, zero-LLM) | compat.evidence_loader |
| fundamental-analyst | quote-fetcher | overseas-market-scanner, evidence-loader | valuation_score, mv_tier | quant.fundamentals |
| technical-analyst | quote-fetcher | market-scanner, mainline-classifier, overseas-market-scanner, sector-rotation-analyzer, evidence-loader | MA/vol/support-resistance, technical_score | pv.technical |
| whale-analyst | quote-fetcher | evidence-loader, f10-reader | whale_score, chip concentration, sentiment_label | pv.microstructure |
| bull-advocate | fundamental-analyst, technical-analyst, whale-analyst | evidence-loader | thesis_bullets [V#], target_price_high/base | dec.bull |
| bear-advocate | fundamental-analyst, technical-analyst, whale-analyst, bull-advocate | evidence-loader | thesis_bullets [F#], target_price_low, downside_pct | dec.bear |
| risk-officer | bull-advocate, bear-advocate | news-reader, f10-reader, evidence-loader | risk_score, veto_flags, position_sizing_advice | dec.risk_debate |
| report-writer | quote-fetcher, fundamental-analyst, technical-analyst, whale-analyst, bull-advocate, bear-advocate, risk-officer | market-scanner, mainline-classifier, morning-brief-writer, overseas-market-scanner, sector-rotation-analyzer, news-sentiment, evidence-loader | ReportOutput: rating_overall, action, target/stop, position_pct | dec.research_mgr |
| introspector | report-writer, bull-advocate, bear-advocate, risk-officer, fundamental-analyst, technical-analyst, whale-analyst | evidence-loader, quote-fetcher | IntrospectionOutput: provenance_violations, quality_flags, proposals | x.number_critic |

