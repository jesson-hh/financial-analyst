# lane0.factor_report.renderer — rendered factor-report untrusted block

Trusted renderer handler wrapping `guanlan_v2.orchestration.market.factors.
render_factor_report_for_prompt` (Phase 5 Task 3b). Pure and deterministic: the
same `MarketFactorReport` renders byte-identical text; no clock, no I/O.

The rendered block is the ONLY numeric surface a Lane 0 LLM worker ever sees —
the LLM never receives the raw typed payload (①§0). The block is delivered on
the untrusted-data channel (`trust="untrusted_data"`,
`rendered_from_payload_digest` bound to the report's content digest); its
header declares as_of / clock_mode / universe_registry_version /
battery_digest / factor_report_digest prefixes, every OK/DEGRADED factor
renders its ≤60-session series grouped by family, and every UNAVAILABLE factor
gets an explicit absence line (absence is information). Length is bounded with
a fail-closed `FactorReportRenderError` on overflow — there is no truncation
path.
